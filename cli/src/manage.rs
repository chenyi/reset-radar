use crate::client::{Error, Http, Result};
use crate::{Skill, Source};
use flate2::read::GzDecoder;
use semver::Version;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fs::{self, File, OpenOptions};
use std::io::Read;
use std::os::unix::fs::{symlink, PermissionsExt};
use std::path::{Component, Path, PathBuf};
use std::process::Command;

type Files = BTreeMap<String, Vec<u8>>;
const MAX_DOWNLOAD: u64 = 64 * 1024 * 1024;

fn invalid(message: impl ToString) -> Error {
    Error::new(6, "installation", message)
}
fn hash(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn target() -> &'static str {
    if cfg!(all(target_os = "macos", target_arch = "aarch64")) {
        "aarch64-apple-darwin"
    } else if cfg!(all(target_os = "linux", target_arch = "x86_64")) {
        "x86_64-unknown-linux-musl"
    } else {
        "unsupported"
    }
}

#[derive(Clone, Deserialize)]
struct Artifact {
    file: String,
    sha256: String,
    #[serde(default)]
    binary_sha256: Option<String>,
}

#[derive(Deserialize)]
struct Manifest {
    version: String,
    api_version: String,
    artifacts: BTreeMap<String, Artifact>,
    skill: Artifact,
}

#[derive(Deserialize, Serialize)]
struct Receipt {
    version: String,
    target: String,
    binary_sha256: String,
    skill: Option<BTreeMap<String, String>>,
}

struct Layout {
    root: PathBuf,
    bin: PathBuf,
    skill: PathBuf,
}

impl Layout {
    fn new() -> Result<Self> {
        let home = std::env::var_os("HOME").ok_or_else(|| invalid("HOME 未设置"))?;
        let home = PathBuf::from(home);
        if !home.is_absolute() {
            return Err(invalid("HOME 必须是绝对路径"));
        }
        Ok(Self {
            root: home.join(".local/share/reset-radar"),
            bin: home.join(".local/bin/reset-radar"),
            skill: home.join(".agents/skills/reset-radar"),
        })
    }
    fn current(&self) -> PathBuf {
        self.root.join("current")
    }
    fn bin_target(&self) -> PathBuf {
        self.current().join("reset-radar")
    }
    fn skill_target(&self) -> PathBuf {
        self.current().join("skill")
    }
    fn lock(&self) -> Result<File> {
        fs::create_dir_all(self.root.join("versions"))?;
        let lock = OpenOptions::new()
            .create(true)
            .truncate(false)
            .write(true)
            .open(self.root.join("install.lock"))?;
        fs2::FileExt::try_lock_exclusive(&lock).map_err(|_| invalid("另一个安装或更新正在运行"))?;
        Ok(lock)
    }
    fn receipt(&self) -> Result<Option<Receipt>> {
        if fs::symlink_metadata(self.current()).is_err() {
            return Ok(None);
        }
        let path = fs::read_link(self.current())
            .map_err(|_| invalid("安装状态目录不是受管理的版本链接"))?;
        if path.parent() != Some(self.root.join("versions").as_path()) {
            return Err(invalid("安装版本链接指向非受管理目录"));
        }
        let bytes = fs::read(self.current().join("receipt.json"))?;
        Ok(Some(
            serde_json::from_slice(&bytes).map_err(|e| invalid(format!("安装记录无效：{e}")))?,
        ))
    }
    fn verify(&self, receipt: Option<&Receipt>) -> Result<()> {
        if let Some(r) = receipt {
            if fs::read_link(&self.bin).ok().as_ref() != Some(&self.bin_target())
                || hash(&fs::read(self.bin_target())?) != r.binary_sha256
            {
                return Err(invalid("CLI 已被修改或同名路径不属于本工具，停止覆盖"));
            }
            if let Some(expected) = &r.skill {
                if fs::read_link(&self.skill).ok().as_ref() != Some(&self.skill_target())
                    || file_hashes(&read_files(&self.skill_target())?) != *expected
                {
                    return Err(invalid("Skill 已被修改，保留用户内容并停止覆盖"));
                }
            } else if fs::symlink_metadata(&self.skill).is_ok() {
                return Err(invalid("同名 Skill 不属于本工具，停止覆盖"));
            }
        } else if fs::symlink_metadata(&self.bin).is_ok()
            || fs::symlink_metadata(&self.skill).is_ok()
        {
            return Err(invalid("发现未受管理的同名 CLI 或 Skill，停止覆盖"));
        }
        Ok(())
    }
}

fn file_hashes(files: &Files) -> BTreeMap<String, String> {
    files.iter().map(|(k, v)| (k.clone(), hash(v))).collect()
}

fn read_files(root: &Path) -> Result<Files> {
    fn walk(root: &Path, dir: &Path, files: &mut Files) -> Result<()> {
        for item in fs::read_dir(dir)? {
            let item = item?;
            let path = item.path();
            let kind = item.file_type()?;
            if kind.is_dir() {
                walk(root, &path, files)?;
            } else if kind.is_file() {
                files.insert(
                    path.strip_prefix(root).unwrap().to_string_lossy().into(),
                    fs::read(&path)?,
                );
            } else {
                return Err(invalid("受管理目录包含意外的符号链接或特殊文件"));
            }
        }
        Ok(())
    }
    let mut files = Files::new();
    walk(root, root, &mut files)?;
    Ok(files)
}

struct Releases<'a> {
    http: &'a Http,
    source: Source,
    site: String,
    github: String,
    github_api: String,
}

impl<'a> Releases<'a> {
    fn new(http: &'a Http, source: Source) -> Self {
        Self {
            http,
            source,
            site: std::env::var("RESET_RADAR_DOWNLOAD_BASE")
                .unwrap_or("https://radar.shadowearth.cn/downloads".into())
                .trim_end_matches('/')
                .into(),
            github: std::env::var("RESET_RADAR_GITHUB_DOWNLOAD_BASE")
                .unwrap_or("https://github.com/chenyi/reset-radar/releases/download".into())
                .trim_end_matches('/')
                .into(),
            github_api: std::env::var("RESET_RADAR_GITHUB_API")
                .unwrap_or("https://api.github.com/repos/chenyi/reset-radar/releases".into()),
        }
    }
    fn latest(&self) -> Result<String> {
        if !matches!(self.source, Source::Github) {
            match self.http.json(&format!("{}/cli/stable.json", self.site)) {
                Ok(body) => {
                    let v = valid_version(body["version"].as_str().unwrap_or(""))?;
                    if !v.pre.is_empty() {
                        return Err(invalid("稳定入口不能指向预发布版本"));
                    }
                    return Ok(v.to_string());
                }
                Err(e) if matches!(self.source, Source::Site) => return Err(e),
                Err(_) => (),
            }
        }
        let mut best: Option<Version> = None;
        let mut page = 1;
        loop {
            let body = self
                .http
                .json(&format!("{}?per_page=100&page={page}", self.github_api))?;
            let releases = body
                .as_array()
                .ok_or_else(|| invalid("GitHub Release 响应无效"))?;
            for release in releases {
                if release["draft"] == true || release["prerelease"] == true {
                    continue;
                }
                if let Some(tag) = release["tag_name"]
                    .as_str()
                    .and_then(|s| s.strip_prefix("cli-v"))
                {
                    if let Ok(v) = Version::parse(tag) {
                        if v.pre.is_empty() && best.as_ref().is_none_or(|old| v > *old) {
                            best = Some(v);
                        }
                    }
                }
            }
            if releases.len() < 100 {
                break;
            }
            page += 1;
        }
        best.map(|v| v.to_string())
            .ok_or_else(|| invalid("没有已发布的稳定 CLI 版本"))
    }
    fn get(&self, version: &str, file: &str) -> Result<Vec<u8>> {
        valid_version(version)?;
        if file.is_empty() || file.contains('/') || file.contains('\\') || file.contains("..") {
            return Err(invalid("发布文件名无效"));
        }
        let suffix = format!("cli-v{version}/{file}");
        if !matches!(self.source, Source::Github) {
            match self
                .http
                .bytes(&format!("{}/{suffix}", self.site), MAX_DOWNLOAD)
            {
                Ok(data) => return Ok(data),
                Err(e) if matches!(self.source, Source::Site) => return Err(e),
                Err(_) => (),
            }
        }
        self.http
            .bytes(&format!("{}/{suffix}", self.github), MAX_DOWNLOAD)
    }
    fn manifest(&self, version: &str) -> Result<Manifest> {
        let result: Manifest = serde_json::from_slice(&self.get(version, "release-manifest.json")?)
            .map_err(|e| invalid(format!("发布清单无效：{e}")))?;
        if result.version != version || result.api_version != "1" {
            return Err(invalid("发布版本或 API 版本不匹配"));
        }
        Ok(result)
    }
    fn artifact(&self, version: &str, artifact: &Artifact, skill: bool) -> Result<Files> {
        let bytes = self.get(version, &artifact.file)?;
        if hash(&bytes) != artifact.sha256 {
            return Err(Error::new(
                6,
                "checksum_mismatch",
                "下载校验失败，保留旧版本",
            ));
        }
        let mut archive = tar::Archive::new(GzDecoder::new(&bytes[..]));
        let mut files = Files::new();
        let mut total = 0;
        for entry in archive.entries()? {
            let mut entry = entry?;
            let path = entry.path()?.into_owned();
            if path
                .components()
                .any(|p| !matches!(p, Component::Normal(_)))
            {
                return Err(invalid("归档路径无效"));
            }
            if entry.header().entry_type().is_dir() {
                continue;
            }
            if !entry.header().entry_type().is_file() {
                return Err(invalid("归档包含非普通文件"));
            }
            let relative = if skill {
                path.strip_prefix("reset-radar")
                    .map_err(|_| invalid("Skill 归档根目录无效"))?
            } else {
                path.as_path()
            };
            let name = relative.to_string_lossy().to_string();
            if name.is_empty() || (!skill && name != "reset-radar") {
                return Err(invalid("归档包含意外文件"));
            }
            total += entry.size();
            if total > MAX_DOWNLOAD {
                return Err(invalid("解压内容超出限制"));
            }
            let mut content = Vec::new();
            entry.read_to_end(&mut content)?;
            if files.insert(name, content).is_some() {
                return Err(invalid("归档包含重复文件"));
            }
        }
        if skill && !files.contains_key("SKILL.md") {
            return Err(invalid("Skill 缺少 SKILL.md"));
        }
        if !skill && !files.contains_key("reset-radar") {
            return Err(invalid("归档缺少 CLI"));
        }
        Ok(files)
    }
}

fn valid_version(value: &str) -> Result<Version> {
    let v = Version::parse(value).map_err(|_| {
        Error::new(
            2,
            "invalid_version",
            "版本应为 0.1.0 或 0.1.0-rc.1，不含 cli-v 前缀",
        )
    })?;
    if !v.build.is_empty() || v.to_string() != value {
        return Err(invalid("发布版本格式无效"));
    }
    Ok(v)
}

fn replace_link(target: &Path, link: &Path) -> Result<()> {
    fs::create_dir_all(link.parent().unwrap())?;
    let temporary = tempfile::NamedTempFile::new_in(link.parent().unwrap())?.into_temp_path();
    fs::remove_file(&temporary)?;
    symlink(target, &temporary)?;
    fs::rename(&temporary, link)?;
    Ok(())
}

fn commit(layout: &Layout, binary: &[u8], skill: Option<Files>, version: &str) -> Result<Value> {
    let temp = tempfile::Builder::new()
        .prefix(&format!("{version}-"))
        .tempdir_in(layout.root.join("versions"))?;
    let binary_path = temp.path().join("reset-radar");
    fs::write(&binary_path, binary)?;
    fs::set_permissions(&binary_path, fs::Permissions::from_mode(0o755))?;
    let output = Command::new(&binary_path).arg("--version").output()?;
    if !output.status.success()
        || String::from_utf8_lossy(&output.stdout).trim() != format!("reset-radar {version}")
    {
        return Err(invalid("新二进制启动或版本检查失败，保留旧版本"));
    }
    if let Some(files) = &skill {
        for (name, content) in files {
            let path = temp.path().join("skill").join(name);
            fs::create_dir_all(path.parent().unwrap())?;
            fs::write(path, content)?;
        }
    }
    let receipt = Receipt {
        version: version.into(),
        target: target().into(),
        binary_sha256: hash(binary),
        skill: skill.as_ref().map(file_hashes),
    };
    fs::write(
        temp.path().join("receipt.json"),
        serde_json::to_vec_pretty(&receipt).unwrap(),
    )?;
    let previous = fs::read_link(layout.current()).ok();
    let bin_existed = fs::symlink_metadata(&layout.bin).is_ok();
    let skill_existed = fs::symlink_metadata(&layout.skill).is_ok();
    let prepared = temp.keep();
    let change = (|| -> Result<()> {
        replace_link(&prepared, &layout.current())?;
        if !bin_existed {
            replace_link(&layout.bin_target(), &layout.bin)?;
        }
        if skill.is_some() && !skill_existed {
            replace_link(&layout.skill_target(), &layout.skill)?;
        }
        if skill.is_none() && skill_existed {
            fs::remove_file(&layout.skill)?;
        }
        Ok(())
    })();
    if let Err(error) = change {
        if let Some(old) = previous {
            replace_link(&old, &layout.current())?;
        } else if layout.current().is_symlink() {
            fs::remove_file(layout.current())?;
        }
        if !bin_existed && layout.bin.is_symlink() {
            fs::remove_file(&layout.bin)?;
        }
        if !skill_existed && layout.skill.is_symlink() {
            fs::remove_file(&layout.skill)?;
        }
        fs::remove_dir_all(prepared)?;
        return Err(error);
    }
    Ok(
        json!({"changed":true,"version":version,"binary":layout.bin,"skill_installed":skill.is_some(),
              "skill_path":if skill.is_some(){Some(&layout.skill)}else{None},"path_note":"PATH 未刷新时可使用 binary 的绝对路径"}),
    )
}

pub fn install(http: &Http, source: Source, with_skill: bool) -> Result<Value> {
    let layout = Layout::new()?;
    let _lock = layout.lock()?;
    let existing = layout.receipt()?;
    layout.verify(existing.as_ref())?;
    let releases = Releases::new(http, source);
    let version = env!("CARGO_PKG_VERSION");
    let manifest = releases.manifest(version)?;
    let artifact = manifest
        .artifacts
        .get(target())
        .ok_or_else(|| invalid("当前平台没有发布产物"))?;
    let binary = fs::read(std::env::current_exe()?)?;
    if artifact.binary_sha256.as_deref() != Some(hash(&binary).as_str()) {
        return Err(invalid("安装器与已发布 CLI 不一致，请使用正式下载产物"));
    }
    let want_skill = with_skill || existing.as_ref().is_some_and(|r| r.skill.is_some());
    if existing
        .as_ref()
        .is_some_and(|r| r.version == version && r.skill.is_some() == want_skill)
    {
        return Ok(
            json!({"changed":false,"version":version,"binary":layout.bin,"skill_installed":want_skill}),
        );
    }
    let skill = if want_skill {
        Some(releases.artifact(version, &manifest.skill, true)?)
    } else {
        None
    };
    commit(&layout, &binary, skill, version)
}

pub fn update(http: &Http, source: Source, requested: Option<&str>, check: bool) -> Result<Value> {
    let layout = Layout::new()?;
    let _lock = layout.lock()?;
    let receipt = layout
        .receipt()?
        .ok_or_else(|| invalid("请先运行安装器或 install"))?;
    layout.verify(Some(&receipt))?;
    let releases = Releases::new(http, source);
    let version = if let Some(v) = requested {
        valid_version(v)?;
        v.into()
    } else {
        releases.latest()?
    };
    let installed = Version::parse(&receipt.version).map_err(|_| invalid("安装记录版本无效"))?;
    let available = valid_version(&version)?;
    if check || version == receipt.version || (requested.is_none() && available < installed) {
        return Ok(
            json!({"changed":false,"installed_version":receipt.version,"available_version":version,
                         "update_available":available > installed}),
        );
    }
    let manifest = releases.manifest(&version)?;
    let artifact = manifest
        .artifacts
        .get(target())
        .ok_or_else(|| invalid("当前平台没有发布产物"))?;
    let binary = releases.artifact(&version, artifact, false)?;
    let skill = if receipt.skill.is_some() {
        Some(releases.artifact(&version, &manifest.skill, true)?)
    } else {
        None
    };
    commit(&layout, &binary["reset-radar"], skill, &version)
}

pub fn skill(http: &Http, source: Source, command: &Skill) -> Result<Value> {
    let layout = Layout::new()?;
    if matches!(command, Skill::Status) {
        let receipt = layout.receipt()?;
        layout.verify(receipt.as_ref())?;
        return Ok(
            json!({"installed":receipt.as_ref().is_some_and(|r| r.skill.is_some()),"path":layout.skill,
                         "cli_version":receipt.map(|r|r.version)}),
        );
    }
    let _lock = layout.lock()?;
    let receipt = layout.receipt()?.ok_or_else(|| invalid("请先安装 CLI"))?;
    layout.verify(Some(&receipt))?;
    if matches!(command, Skill::Remove) {
        if receipt.skill.is_none() {
            return Ok(json!({"changed":false,"installed":false}));
        }
        return commit(
            &layout,
            &fs::read(layout.bin_target())?,
            None,
            &receipt.version,
        );
    }
    if matches!(command, Skill::Update) && receipt.skill.is_none() {
        return Err(invalid(
            "Skill 尚未安装，请使用 skill install --agent codex",
        ));
    }
    let releases = Releases::new(http, source);
    let manifest = releases.manifest(&receipt.version)?;
    let files = releases.artifact(&receipt.version, &manifest.skill, true)?;
    if receipt.skill.as_ref() == Some(&file_hashes(&files)) {
        return Ok(
            json!({"changed":false,"version":receipt.version,"installed":true,"note":"Skill 已与 CLI 配套；升级整套工具使用 update"}),
        );
    }
    commit(
        &layout,
        &fs::read(layout.bin_target())?,
        Some(files),
        &receipt.version,
    )
}
