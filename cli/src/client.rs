use reqwest::blocking::Client;
use serde_json::{json, Value};
use std::io::Read;
use std::time::Duration;

#[derive(Debug)]
pub struct Error {
    pub exit: i32,
    pub code: String,
    pub message: String,
}

impl Error {
    pub fn new(exit: i32, code: &str, message: impl ToString) -> Self {
        Self {
            exit,
            code: code.into(),
            message: message.to_string(),
        }
    }
    pub fn json(&self) -> Value {
        json!({"error": {"code": self.code, "message": self.message}})
    }
}

impl From<std::io::Error> for Error {
    fn from(e: std::io::Error) -> Self {
        Self::new(6, "filesystem", e)
    }
}

pub type Result<T> = std::result::Result<T, Error>;

pub fn check_url(url: &str) -> Result<()> {
    let parsed = reqwest::Url::parse(url).map_err(|e| Error::new(2, "invalid_url", e))?;
    let local = matches!(parsed.host_str(), Some("127.0.0.1" | "localhost" | "[::1]"));
    if parsed.scheme() != "https" && !(parsed.scheme() == "http" && local) {
        return Err(Error::new(
            2,
            "invalid_url",
            "需要 HTTPS；HTTP 仅允许本机开发测试",
        ));
    }
    Ok(())
}

pub struct Http {
    client: Client,
}

impl Http {
    pub fn new(timeout: u64) -> Result<Self> {
        if timeout == 0 || timeout > 300 {
            return Err(Error::new(2, "invalid_timeout", "timeout 应为 1–300 秒"));
        }
        let client = Client::builder()
            .timeout(Duration::from_secs(timeout))
            .connect_timeout(Duration::from_secs(timeout.min(10)))
            .user_agent(concat!("reset-radar/", env!("CARGO_PKG_VERSION")))
            .redirect(reqwest::redirect::Policy::custom(|attempt| {
                if attempt.previous().len() >= 5 {
                    attempt.error("too many redirects")
                } else if attempt.url().scheme() == "https" {
                    attempt.follow()
                } else {
                    attempt.error("insecure redirect")
                }
            }))
            .build()
            .map_err(|e| Error::new(3, "network", e))?;
        Ok(Self { client })
    }

    pub fn bytes(&self, url: &str, limit: u64) -> Result<Vec<u8>> {
        check_url(url)?;
        let response = self
            .client
            .get(url)
            .send()
            .map_err(|e| Error::new(3, "network", e))?;
        let status = response.status();
        let mut bytes = Vec::new();
        response
            .take(limit + 1)
            .read_to_end(&mut bytes)
            .map_err(|e| Error::new(3, "network", e))?;
        if bytes.len() as u64 > limit {
            return Err(Error::new(5, "response_too_large", "响应超出大小限制"));
        }
        if !status.is_success() {
            let body = serde_json::from_slice::<Value>(&bytes).unwrap_or(Value::Null);
            let code = body["error"]["code"].as_str().unwrap_or("http_error");
            let message = body["error"]["message"].as_str().unwrap_or("请求失败");
            return Err(Error::new(4, code, format!("HTTP {status}: {message}")));
        }
        Ok(bytes)
    }

    pub fn json(&self, url: &str) -> Result<Value> {
        serde_json::from_slice(&self.bytes(url, 8 * 1024 * 1024)?)
            .map_err(|e| Error::new(5, "invalid_response", e))
    }

    pub fn api(&self, base: &str, path: &str, query: &[(String, String)]) -> Result<Value> {
        let mut url = reqwest::Url::parse(&format!("{}{path}", base.trim_end_matches('/')))
            .map_err(|e| Error::new(2, "invalid_url", e))?;
        url.query_pairs_mut()
            .extend_pairs(query.iter().map(|(k, v)| (k.as_str(), v.as_str())));
        let body = self.json(url.as_str())?;
        if body["api_version"] != "1" || !body["data"].is_object() || !body["meta"].is_object() {
            return Err(Error::new(
                5,
                "incompatible_api",
                "API 主版本或响应格式不兼容",
            ));
        }
        Ok(body)
    }
}
