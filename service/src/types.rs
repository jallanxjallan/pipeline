use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{collections::BTreeMap, str::FromStr};

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct PlanId(pub String);

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PlanSummary {
    pub slug: String,
    pub label: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum ResponseAction {
    Writeback,
    Writenew,
    Export,
    None,
}

impl ResponseAction {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Writeback => "writeback",
            Self::Writenew => "writenew",
            Self::Export => "export",
            Self::None => "none",
        }
    }
}

impl FromStr for ResponseAction {
    type Err = String;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "writeback" => Ok(Self::Writeback),
            "writenew" => Ok(Self::Writenew),
            "export" => Ok(Self::Export),
            "none" => Ok(Self::None),
            other => Err(format!("unsupported response action: {other}")),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PipelineCall {
    #[serde(rename = "type")]
    pub record_type: String,
    pub identity: String,
    pub content: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub directive: Option<String>,
    pub plan: String,
    pub extra: PipelineExtra,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PipelineExtra {
    pub source: SourceContext,
    pub response: ResponseContext,
    #[serde(default)]
    pub assets: Vec<String>,
    #[serde(default)]
    pub context: BTreeMap<String, Value>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SourceContext {
    pub kind: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub path: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub status: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub stage: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub format: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sha256: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub original_path: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ResponseContext {
    pub action: ResponseAction,
}
