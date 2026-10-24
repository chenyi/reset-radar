mod client;
mod manage;

use chrono::{DateTime, Duration, Local, Utc};
use clap::{Parser, Subcommand, ValueEnum};
use client::{Error, Http, Result};
use serde_json::Value;

#[derive(Parser)]
#[command(version, about = "重置雷达：公开预告、完成公告与原帖证据")]
struct Args {
    #[arg(long, global = true, help = "输出纯 JSON")]
    json: bool,
    #[arg(long, global = true, default_value = "https://radar.shadowearth.cn")]
    api_url: String,
    #[arg(long, global = true, default_value_t = 30)]
    timeout: u64,
    #[arg(long, global = true)]
    timezone: Option<chrono_tz::Tz>,
    #[arg(long, global = true, value_enum, default_value = "auto")]
    download_source: Source,
    #[command(subcommand)]
    command: Command,
}

#[derive(Clone, Copy, ValueEnum)]
pub enum Source {
    Auto,
    Site,
    Github,
}

#[derive(Subcommand)]
enum Command {
    /// 当前有效预告与最近一次完成公告
    Status,
    /// 历史事件查询
    Events {
        #[command(subcommand)]
        command: Events,
    },
    /// 安装当前版本到用户目录，可同时安装 Codex Skill
    Install {
        #[arg(long, value_parser = ["codex"])]
        agent: Option<String>,
    },
    /// 更新 CLI 和已安装的 Skill；指定旧版本可回退
    Update {
        #[arg(long)]
        check: bool,
        #[arg(long)]
        version: Option<String>,
    },
    /// 管理重置雷达自己的 Codex Skill
    Skill {
        #[command(subcommand)]
        command: Skill,
    },
}

#[derive(Subcommand)]
enum Events {
    List {
        #[arg(long)]
        since: Option<String>,
        #[arg(long, value_parser = ["reset", "credit"])]
        r#type: Option<String>,
        #[arg(long, value_parser = ["announced", "scheduled", "completed", "cancelled", "unconfirmed"])]
        state: Option<String>,
        #[arg(long, default_value_t = 20, value_parser = clap::value_parser!(u32).range(1..=100))]
        limit: u32,
        #[arg(long)]
        cursor: Option<String>,
    },
    Get {
        id: String,
    },
}

#[derive(Subcommand)]
enum Skill {
    Install {
        #[arg(long, default_value = "codex", value_parser = ["codex"])]
        agent: String,
    },
    Status,
    Update,
    Remove,
}

fn since(value: &str) -> Result<String> {
    if let Ok(time) = DateTime::parse_from_rfc3339(value) {
        return Ok(time
            .with_timezone(&Utc)
            .to_rfc3339_opts(chrono::SecondsFormat::Secs, true));
    }
    let split = value
        .len()
        .checked_sub(1)
        .ok_or_else(|| Error::new(2, "invalid_since", "需要 RFC3339 或 Nh/Nd"))?;
    let (number, unit) = value
        .split_at_checked(split)
        .ok_or_else(|| Error::new(2, "invalid_since", "无效时间"))?;
    let n = number
        .parse::<i64>()
        .map_err(|_| Error::new(2, "invalid_since", "需要 RFC3339 或 Nh/Nd"))?;
    let seconds = match unit {
        "h" => n.checked_mul(3600),
        "d" => n.checked_mul(86400),
        _ => None,
    }
    .filter(|_| n > 0)
    .ok_or_else(|| Error::new(2, "invalid_since", "需要正整数 Nh/Nd"))?;
    let duration = Duration::try_seconds(seconds)
        .ok_or_else(|| Error::new(2, "invalid_since", "时间范围过大"))?;
    Ok(Utc::now()
        .checked_sub_signed(duration)
        .ok_or_else(|| Error::new(2, "invalid_since", "时间范围过大"))?
        .to_rfc3339_opts(chrono::SecondsFormat::Secs, true))
}

fn execute(args: &Args) -> Result<Value> {
    let http = Http::new(args.timeout)?;
    match &args.command {
        Command::Status => http.api(&args.api_url, "/api/v1/status", &[]),
        Command::Events {
            command: Events::Get { id },
        } => {
            let mut url = reqwest::Url::parse("https://example.test/").unwrap();
            url.path_segments_mut().unwrap().push(id);
            http.api(&args.api_url, &format!("/api/v1/events{}", url.path()), &[])
        }
        Command::Events {
            command:
                Events::List {
                    since: start,
                    r#type,
                    state,
                    limit,
                    cursor,
                },
        } => {
            let mut query = vec![("limit".into(), limit.to_string())];
            if let Some(s) = start {
                query.push(("since".into(), since(s)?));
            }
            if let Some(t) = r#type {
                query.push(("type".into(), t.clone()));
            }
            if let Some(s) = state {
                query.push(("state".into(), s.clone()));
            }
            if let Some(c) = cursor {
                query.push(("cursor".into(), c.clone()));
            }
            http.api(&args.api_url, "/api/v1/events", &query)
        }
        Command::Install { agent } => manage::install(&http, args.download_source, agent.is_some()),
        Command::Update { check, version } => {
            manage::update(&http, args.download_source, version.as_deref(), *check)
        }
        Command::Skill { command } => manage::skill(&http, args.download_source, command),
    }
}

fn time_label(value: &Value, args: &Args) -> String {
    let Some(raw) = value.as_str() else {
        return "未明确".into();
    };
    let Ok(time) = DateTime::parse_from_rfc3339(raw) else {
        return raw.into();
    };
    match args.timezone {
        Some(zone) => time
            .with_timezone(&zone)
            .format("%Y-%m-%d %H:%M:%S %Z")
            .to_string(),
        None => time
            .with_timezone(&Local)
            .format("%Y-%m-%d %H:%M:%S %:z")
            .to_string(),
    }
}

fn relative(value: &Value, now: &Value) -> String {
    match (
        value
            .as_str()
            .and_then(|s| DateTime::parse_from_rfc3339(s).ok()),
        now.as_str()
            .and_then(|s| DateTime::parse_from_rfc3339(s).ok()),
    ) {
        (Some(t), Some(n)) => {
            let seconds = (t - n).num_seconds();
            let minutes = seconds.unsigned_abs() / 60;
            format!(
                "{}{}天 {}小时 {}分钟",
                if seconds < 0 {
                    "距今已过 "
                } else {
                    "距今还有 "
                },
                minutes / 1440,
                minutes / 60 % 24,
                minutes % 60
            )
        }
        _ => String::new(),
    }
}

fn print_event(event: &Value, args: &Args, now: &Value) {
    if event.is_null() {
        return;
    }
    println!(
        "事件：{}（{}）",
        event["event_id"].as_str().unwrap_or(""),
        event["state"].as_str().unwrap_or("")
    );
    for (key, label) in [
        ("forecast_announced_at", "预告发布"),
        ("expected_at", "预计时刻"),
        ("deadline_at", "截止时刻"),
        ("completed_announced_at", "完成公告"),
        ("effective_at", "原文明确的实际生效时间"),
    ] {
        if !event[key].is_null() {
            println!(
                "{label}：{} {}",
                time_label(&event[key], args),
                relative(&event[key], now)
            );
        }
    }
    if let Some(phrase) = event["time_quote"].as_str() {
        println!("原始时间表述：{phrase}");
    }
    if let Some(urls) = event["source_urls"].as_array() {
        for url in urls {
            println!("原帖：{}", url.as_str().unwrap_or(""));
        }
    }
    if let Some(evidence) = event["evidence"].as_array() {
        for p in evidence {
            println!(
                "原文 [{}]：{}",
                p["created_at"].as_str().unwrap_or(""),
                p["text"].as_str().unwrap_or("")
            );
            if let Some(zh) = p["text_zh"].as_str() {
                println!("中文译文：{zh}");
            }
        }
    }
}

fn print_text(body: &Value, args: &Args) {
    let data = &body["data"];
    let meta = &body["meta"];
    let now = &meta["generated_at"];
    if data.get("forecast_status").is_some() {
        let status = data["forecast_status"].as_str().unwrap_or("unknown");
        println!(
            "{}",
            match status {
                "active" => "当前有有效重置预告；未给出时间时不能推算倒计时。",
                "none" => "当前可用来源中没有有效重置预告。",
                "elapsed" => "最近预告的预计／截止时间已过，尚未确认完成。",
                _ => "来源过期、不可用或分析尚未跟上，无法确认当前是否有新预告。",
            }
        );
        let forecast = if data["active_forecast"].is_null() {
            &data["latest_forecast"]
        } else {
            &data["active_forecast"]
        };
        print_event(forecast, args, now);
        println!("\n最近一次完成重置：");
        if data["latest_completed"].is_null() {
            println!("没有可靠完成公告记录。");
        } else {
            print_event(&data["latest_completed"], args, now);
            if data["latest_completed"]["effective_at"].is_null() {
                println!("以上为完成公告时间，实际发生时刻未明确。");
            }
        }
        println!("公开公告不代表你的个人额度已经到账。");
    } else if let Some(items) = data["items"].as_array() {
        println!("事件数量：{}", items.len());
        if !data["filters"].is_null() {
            println!("筛选：{}", data["filters"]);
        }
        for item in items {
            print_event(item, args, now);
        }
        if let Some(cursor) = data["next_cursor"].as_str() {
            println!("下一页 --cursor {cursor}（保持相同筛选条件）");
        }
    } else if data.get("event_id").is_some() {
        print_event(data, args, now);
    } else {
        println!("{}", serde_json::to_string_pretty(body).unwrap());
    }
    if !meta.is_null() {
        println!(
            "来源：{}；分析：{}；最近成功检查：{}",
            meta["source_status"].as_str().unwrap_or("unknown"),
            meta["analysis_status"].as_str().unwrap_or("unknown"),
            time_label(&meta["source_last_success_at"], args)
        );
    }
}

fn main() {
    let raw: Vec<String> = std::env::args().collect();
    let args = match Args::try_parse_from(&raw) {
        Ok(a) => a,
        Err(e) => {
            if raw.iter().any(|s| s == "--json") && e.exit_code() != 0 {
                println!(
                    "{}",
                    Error::new(2, "invalid_arguments", e.to_string()).json()
                );
                std::process::exit(2);
            }
            e.exit();
        }
    };
    match execute(&args) {
        Ok(body) => {
            if args.json {
                println!("{body}");
            } else {
                print_text(&body, &args);
            }
        }
        Err(error) => {
            if args.json {
                println!("{}", error.json());
            } else {
                eprintln!("{}: {}", error.code, error.message);
            }
            std::process::exit(error.exit);
        }
    }
}
