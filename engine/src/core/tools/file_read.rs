//! FileRead tool — reads files from the filesystem with path policy enforcement.

use anyhow::{bail, Result};
use async_trait::async_trait;
use serde_json::Value;
use std::collections::HashMap;
use std::path::PathBuf;
use tracing::info;

use crate::core::config::FilesystemPermissions;
use crate::core::tool::Tool;
use crate::core::types::{RiskLevel, SecurityContext, ToolResult, ToolResultMetadata};

/// Maximum file size to read (1 MB).
const MAX_READ_BYTES: u64 = 1_048_576;

/// FileRead tool — reads a file and returns its contents.
pub struct FileReadTool {
    permissions: FilesystemPermissions,
}

impl FileReadTool {
    pub fn new(permissions: FilesystemPermissions) -> Self {
        Self { permissions }
    }
}

/// Check whether a path is allowed by the given glob patterns.
/// Deny list takes precedence over allow list.
fn check_path_policy(
    path: &std::path::Path,
    allow_patterns: &[String],
    deny_patterns: &[String],
) -> Result<()> {
    let path_str = path.to_string_lossy();

    // Deny list takes precedence
    for pattern in deny_patterns {
        if let Ok(pat) = glob::Pattern::new(pattern) {
            if pat.matches(&path_str) {
                bail!("Path denied by policy: {}", path_str);
            }
        }
    }

    // If allow list is empty, allow all (that aren't denied)
    if allow_patterns.is_empty() {
        return Ok(());
    }

    // Check allow list
    for pattern in allow_patterns {
        if let Ok(pat) = glob::Pattern::new(pattern) {
            if pat.matches(&path_str) {
                return Ok(());
            }
        }
    }

    bail!("Path not in allow list: {}", path_str);
}

#[async_trait]
impl Tool for FileReadTool {
    fn name(&self) -> &str {
        "file_read"
    }

    fn description(&self) -> &str {
        "Read a file from the local filesystem. Returns the file contents as text."
    }

    fn parameters_schema(&self) -> Value {
        serde_json::json!({
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or relative path to the file to read"
                }
            },
            "required": ["path"]
        })
    }

    fn risk_level(&self) -> RiskLevel {
        RiskLevel::Low
    }

    fn required_permissions(&self) -> Vec<String> {
        vec!["file.read".to_string()]
    }

    fn validate_args(&self, args: &HashMap<String, Value>) -> Result<()> {
        let path = args
            .get("path")
            .and_then(|v| v.as_str())
            .ok_or_else(|| anyhow::anyhow!("Missing required argument: path"))?;

        if path.is_empty() {
            bail!("Path cannot be empty");
        }
        Ok(())
    }

    async fn execute(
        &self,
        args: &HashMap<String, Value>,
        _ctx: &SecurityContext,
    ) -> Result<ToolResult> {
        let started_at = chrono::Utc::now();
        let start = std::time::Instant::now();

        let path_str = args
            .get("path")
            .and_then(|v| v.as_str())
            .unwrap_or_default();

        let path = PathBuf::from(path_str);

        // Canonicalize to resolve symlinks and ../
        let canonical = match std::fs::canonicalize(&path) {
            Ok(p) => p,
            Err(e) => {
                return Ok(ToolResult {
                    success: false,
                    output: String::new(),
                    error: Some(format!("Cannot resolve path '{}': {}", path_str, e)),
                    metadata: ToolResultMetadata {
                        tool_name: "file_read".into(),
                        duration_ms: start.elapsed().as_millis() as u64,
                        sandboxed: false,
                        risk_level: RiskLevel::Low,
                        execution_id: String::new(),
                        started_at,
                        completed_at: chrono::Utc::now(),
                        exit_code: None,
                        bytes_read: 0,
                        bytes_written: 0,
                        truncated: false,
                        provider_usage: None,
                    },
                });
            }
        };

        // Path policy check
        if let Err(e) = check_path_policy(
            &canonical,
            &self.permissions.read,
            &self.permissions.deny,
        ) {
            return Ok(ToolResult {
                success: false,
                output: String::new(),
                error: Some(e.to_string()),
                metadata: ToolResultMetadata {
                    tool_name: "file_read".into(),
                    duration_ms: start.elapsed().as_millis() as u64,
                    sandboxed: false,
                    risk_level: RiskLevel::Low,
                    execution_id: String::new(),
                    started_at,
                    completed_at: chrono::Utc::now(),
                    exit_code: None,
                    bytes_read: 0,
                    bytes_written: 0,
                    truncated: false,
                    provider_usage: None,
                },
            });
        }

        // Check file size
        let metadata = match std::fs::metadata(&canonical) {
            Ok(m) => m,
            Err(e) => {
                return Ok(ToolResult {
                    success: false,
                    output: String::new(),
                    error: Some(format!("Cannot stat '{}': {}", path_str, e)),
                    metadata: ToolResultMetadata {
                        tool_name: "file_read".into(),
                        duration_ms: start.elapsed().as_millis() as u64,
                        sandboxed: false,
                        risk_level: RiskLevel::Low,
                        execution_id: String::new(),
                        started_at,
                        completed_at: chrono::Utc::now(),
                        exit_code: None,
                        bytes_read: 0,
                        bytes_written: 0,
                        truncated: false,
                        provider_usage: None,
                    },
                });
            }
        };

        let file_size = metadata.len();
        let truncated = file_size > MAX_READ_BYTES;

        // Read file content
        let content = match std::fs::read_to_string(&canonical) {
            Ok(c) => {
                if truncated {
                    c.chars().take(MAX_READ_BYTES as usize).collect::<String>()
                } else {
                    c
                }
            }
            Err(e) => {
                return Ok(ToolResult {
                    success: false,
                    output: String::new(),
                    error: Some(format!("Cannot read '{}': {}", path_str, e)),
                    metadata: ToolResultMetadata {
                        tool_name: "file_read".into(),
                        duration_ms: start.elapsed().as_millis() as u64,
                        sandboxed: false,
                        risk_level: RiskLevel::Low,
                        execution_id: String::new(),
                        started_at,
                        completed_at: chrono::Utc::now(),
                        exit_code: None,
                        bytes_read: 0,
                        bytes_written: 0,
                        truncated: false,
                        provider_usage: None,
                    },
                });
            }
        };

        let bytes_read = content.len() as u64;
        let output = if truncated {
            format!(
                "{}\n\n[Truncated: file is {} bytes, showing first {} bytes]",
                content, file_size, MAX_READ_BYTES
            )
        } else {
            content
        };

        info!(path = %path_str, bytes = bytes_read, "file_read completed");

        Ok(ToolResult {
            success: true,
            output,
            error: None,
            metadata: ToolResultMetadata {
                tool_name: "file_read".into(),
                duration_ms: start.elapsed().as_millis() as u64,
                sandboxed: false,
                risk_level: RiskLevel::Low,
                execution_id: String::new(),
                started_at,
                completed_at: chrono::Utc::now(),
                exit_code: None,
                bytes_read,
                bytes_written: 0,
                truncated,
                provider_usage: None,
            },
        })
    }

    fn is_cacheable(&self) -> bool {
        true
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use tempfile::NamedTempFile;

    fn test_permissions() -> FilesystemPermissions {
        FilesystemPermissions {
            read: vec![],
            write: vec![],
            deny: vec![
                "/etc/shadow".to_string(),
                "/etc/passwd".to_string(),
            ],
        }
    }

    #[test]
    fn test_check_path_policy_deny() {
        let path = std::path::Path::new("/etc/shadow");
        let result = check_path_policy(path, &[], &["/etc/shadow".to_string()]);
        assert!(result.is_err());
    }

    #[test]
    fn test_check_path_policy_allow_empty() {
        let path = std::path::Path::new("/tmp/test.txt");
        let result = check_path_policy(path, &[], &[]);
        assert!(result.is_ok());
    }

    #[test]
    fn test_check_path_policy_allow_match() {
        let path = std::path::Path::new("/workspace/staging/test.py");
        let result = check_path_policy(
            path,
            &["/workspace/staging/**".to_string()],
            &[],
        );
        assert!(result.is_ok());
    }

    #[test]
    fn test_check_path_policy_allow_no_match() {
        let path = std::path::Path::new("/root/.ssh/id_rsa");
        let result = check_path_policy(
            path,
            &["/workspace/**".to_string()],
            &[],
        );
        assert!(result.is_err());
    }

    #[test]
    fn test_check_path_policy_deny_precedence() {
        let path = std::path::Path::new("/workspace/staging/.env");
        let result = check_path_policy(
            path,
            &["/workspace/**".to_string()],
            &["/workspace/staging/.env".to_string()],
        );
        assert!(result.is_err());
    }

    #[tokio::test]
    async fn test_file_read_success() {
        let mut tmpfile = NamedTempFile::new().unwrap();
        write!(tmpfile, "hello world").unwrap();
        let path = tmpfile.path().to_string_lossy().to_string();

        let tool = FileReadTool::new(test_permissions());
        let ctx = SecurityContext::new("test".into());
        let mut args = HashMap::new();
        args.insert("path".into(), Value::String(path));

        let result = tool.execute(&args, &ctx).await.unwrap();
        assert!(result.success);
        assert_eq!(result.output, "hello world");
        assert_eq!(result.metadata.bytes_read, 11);
        assert!(!result.metadata.truncated);
    }

    #[tokio::test]
    async fn test_file_read_not_found() {
        let tool = FileReadTool::new(test_permissions());
        let ctx = SecurityContext::new("test".into());
        let mut args = HashMap::new();
        args.insert("path".into(), Value::String("/tmp/nonexistent_ironclaw_test_file.txt".into()));

        let result = tool.execute(&args, &ctx).await.unwrap();
        assert!(!result.success);
        assert!(result.error.is_some());
    }

    #[tokio::test]
    async fn test_file_read_denied_path() {
        // Create a temp file then add its canonicalized path to deny list
        let mut tmpfile = NamedTempFile::new().unwrap();
        write!(tmpfile, "secret").unwrap();
        let path = tmpfile.path().to_string_lossy().to_string();
        // Use the canonicalized form for the deny pattern (matches what execute() checks)
        let canonical = std::fs::canonicalize(tmpfile.path())
            .unwrap()
            .to_string_lossy()
            .to_string();

        let perms = FilesystemPermissions {
            read: vec![],
            write: vec![],
            deny: vec![canonical],
        };
        let tool = FileReadTool::new(perms);
        let ctx = SecurityContext::new("test".into());
        let mut args = HashMap::new();
        args.insert("path".into(), Value::String(path));

        let result = tool.execute(&args, &ctx).await.unwrap();
        assert!(!result.success);
        assert!(result.error.as_ref().unwrap().contains("denied"));
    }

    #[test]
    fn test_validate_args_missing_path() {
        let tool = FileReadTool::new(test_permissions());
        let args = HashMap::new();
        assert!(tool.validate_args(&args).is_err());
    }

    #[test]
    fn test_validate_args_empty_path() {
        let tool = FileReadTool::new(test_permissions());
        let mut args = HashMap::new();
        args.insert("path".into(), Value::String(String::new()));
        assert!(tool.validate_args(&args).is_err());
    }

    #[test]
    fn test_tool_metadata() {
        let tool = FileReadTool::new(test_permissions());
        assert_eq!(tool.name(), "file_read");
        assert_eq!(tool.risk_level(), RiskLevel::Low);
        assert!(tool.is_cacheable());
    }
}
