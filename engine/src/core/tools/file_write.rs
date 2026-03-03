//! FileWrite tool — writes files to the filesystem with path policy enforcement.

use anyhow::{bail, Result};
use async_trait::async_trait;
use serde_json::Value;
use std::collections::HashMap;
use std::path::PathBuf;
use tracing::info;

use crate::core::config::FilesystemPermissions;
use crate::core::tool::Tool;
use crate::core::types::{RiskLevel, SecurityContext, ToolResult, ToolResultMetadata};

/// FileWrite tool — writes content to a file, creating parent directories.
pub struct FileWriteTool {
    permissions: FilesystemPermissions,
}

impl FileWriteTool {
    pub fn new(permissions: FilesystemPermissions) -> Self {
        Self { permissions }
    }
}

/// Check whether a write path is allowed.
/// Deny list takes precedence over allow list.
fn check_write_policy(
    path: &std::path::Path,
    allow_patterns: &[String],
    deny_patterns: &[String],
) -> Result<()> {
    let path_str = path.to_string_lossy();

    // Deny list takes precedence
    for pattern in deny_patterns {
        if let Ok(pat) = glob::Pattern::new(pattern) {
            if pat.matches(&path_str) {
                bail!("Write denied by policy: {}", path_str);
            }
        }
    }

    // If allow list is empty, deny all writes (secure default)
    if allow_patterns.is_empty() {
        bail!("No write paths configured — all writes denied");
    }

    // Check allow list
    for pattern in allow_patterns {
        if let Ok(pat) = glob::Pattern::new(pattern) {
            if pat.matches(&path_str) {
                return Ok(());
            }
        }
    }

    bail!("Write path not in allow list: {}", path_str);
}

#[async_trait]
impl Tool for FileWriteTool {
    fn name(&self) -> &str {
        "file_write"
    }

    fn description(&self) -> &str {
        "Write content to a file. Creates parent directories if needed."
    }

    fn parameters_schema(&self) -> Value {
        serde_json::json!({
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or relative path for the file to write"
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file"
                }
            },
            "required": ["path", "content"]
        })
    }

    fn risk_level(&self) -> RiskLevel {
        RiskLevel::High
    }

    fn required_permissions(&self) -> Vec<String> {
        vec!["file.write".to_string()]
    }

    fn validate_args(&self, args: &HashMap<String, Value>) -> Result<()> {
        let path = args
            .get("path")
            .and_then(|v| v.as_str())
            .ok_or_else(|| anyhow::anyhow!("Missing required argument: path"))?;

        if path.is_empty() {
            bail!("Path cannot be empty");
        }

        args.get("content")
            .and_then(|v| v.as_str())
            .ok_or_else(|| anyhow::anyhow!("Missing required argument: content"))?;

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

        let content = args
            .get("content")
            .and_then(|v| v.as_str())
            .unwrap_or_default();

        let path = PathBuf::from(path_str);

        // For write, we check the path as-is (not canonicalized, since file
        // may not exist yet). We do normalize to resolve ../ components.
        let normalized = if path.is_absolute() {
            path.clone()
        } else {
            std::env::current_dir()
                .unwrap_or_else(|_| PathBuf::from("/"))
                .join(&path)
        };

        // Clean the path (resolve ../ without requiring file to exist)
        let mut cleaned = PathBuf::new();
        for component in normalized.components() {
            match component {
                std::path::Component::ParentDir => { cleaned.pop(); }
                std::path::Component::CurDir => {}
                other => cleaned.push(other),
            }
        }

        // Write policy check
        if let Err(e) = check_write_policy(
            &cleaned,
            &self.permissions.write,
            &self.permissions.deny,
        ) {
            return Ok(ToolResult {
                success: false,
                output: String::new(),
                error: Some(e.to_string()),
                metadata: ToolResultMetadata {
                    tool_name: "file_write".into(),
                    duration_ms: start.elapsed().as_millis() as u64,
                    sandboxed: false,
                    risk_level: RiskLevel::High,
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

        // Create parent directories
        if let Some(parent) = cleaned.parent() {
            if let Err(e) = std::fs::create_dir_all(parent) {
                return Ok(ToolResult {
                    success: false,
                    output: String::new(),
                    error: Some(format!("Cannot create parent directory: {}", e)),
                    metadata: ToolResultMetadata {
                        tool_name: "file_write".into(),
                        duration_ms: start.elapsed().as_millis() as u64,
                        sandboxed: false,
                        risk_level: RiskLevel::High,
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
        }

        // Write file
        let bytes_written = content.len() as u64;
        match std::fs::write(&cleaned, content) {
            Ok(()) => {
                info!(path = %path_str, bytes = bytes_written, "file_write completed");

                Ok(ToolResult {
                    success: true,
                    output: format!(
                        "File written: {} ({} bytes)",
                        path_str, bytes_written
                    ),
                    error: None,
                    metadata: ToolResultMetadata {
                        tool_name: "file_write".into(),
                        duration_ms: start.elapsed().as_millis() as u64,
                        sandboxed: false,
                        risk_level: RiskLevel::High,
                        execution_id: String::new(),
                        started_at,
                        completed_at: chrono::Utc::now(),
                        exit_code: None,
                        bytes_read: 0,
                        bytes_written,
                        truncated: false,
                        provider_usage: None,
                    },
                })
            }
            Err(e) => Ok(ToolResult {
                success: false,
                output: String::new(),
                error: Some(format!("Write failed: {}", e)),
                metadata: ToolResultMetadata {
                    tool_name: "file_write".into(),
                    duration_ms: start.elapsed().as_millis() as u64,
                    sandboxed: false,
                    risk_level: RiskLevel::High,
                    execution_id: String::new(),
                    started_at,
                    completed_at: chrono::Utc::now(),
                    exit_code: None,
                    bytes_read: 0,
                    bytes_written: 0,
                    truncated: false,
                    provider_usage: None,
                },
            }),
        }
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    fn test_permissions(write_dir: &str) -> FilesystemPermissions {
        FilesystemPermissions {
            read: vec![],
            write: vec![format!("{}/**", write_dir)],
            deny: vec![
                "/etc/**".to_string(),
                "**/.env".to_string(),
            ],
        }
    }

    #[test]
    fn test_check_write_policy_deny() {
        let path = std::path::Path::new("/etc/passwd");
        let result = check_write_policy(
            path,
            &["/workspace/**".to_string()],
            &["/etc/**".to_string()],
        );
        assert!(result.is_err());
        assert!(result.unwrap_err().to_string().contains("denied"));
    }

    #[test]
    fn test_check_write_policy_no_allow() {
        let path = std::path::Path::new("/tmp/test.txt");
        let result = check_write_policy(path, &[], &[]);
        assert!(result.is_err());
        assert!(result.unwrap_err().to_string().contains("No write paths"));
    }

    #[test]
    fn test_check_write_policy_allow_match() {
        let path = std::path::Path::new("/workspace/staging/output/test.py");
        let result = check_write_policy(
            path,
            &["/workspace/staging/**".to_string()],
            &[],
        );
        assert!(result.is_ok());
    }

    #[test]
    fn test_check_write_policy_deny_env() {
        let path = std::path::Path::new("/workspace/staging/.env");
        let result = check_write_policy(
            path,
            &["/workspace/**".to_string()],
            &["**/.env".to_string()],
        );
        assert!(result.is_err());
    }

    #[tokio::test]
    async fn test_file_write_success() {
        let tmpdir = TempDir::new().unwrap();
        let file_path = tmpdir.path().join("test.txt");
        let dir_str = tmpdir.path().to_string_lossy().to_string();

        let tool = FileWriteTool::new(test_permissions(&dir_str));
        let ctx = SecurityContext::new("test".into());
        let mut args = HashMap::new();
        args.insert("path".into(), Value::String(file_path.to_string_lossy().to_string()));
        args.insert("content".into(), Value::String("hello world".into()));

        let result = tool.execute(&args, &ctx).await.unwrap();
        assert!(result.success, "Error: {:?}", result.error);
        assert!(result.output.contains("File written"));
        assert_eq!(result.metadata.bytes_written, 11);

        // Verify file contents
        let contents = std::fs::read_to_string(&file_path).unwrap();
        assert_eq!(contents, "hello world");
    }

    #[tokio::test]
    async fn test_file_write_creates_parent_dirs() {
        let tmpdir = TempDir::new().unwrap();
        let file_path = tmpdir.path().join("sub").join("dir").join("test.txt");
        let dir_str = tmpdir.path().to_string_lossy().to_string();

        let tool = FileWriteTool::new(test_permissions(&dir_str));
        let ctx = SecurityContext::new("test".into());
        let mut args = HashMap::new();
        args.insert("path".into(), Value::String(file_path.to_string_lossy().to_string()));
        args.insert("content".into(), Value::String("nested".into()));

        let result = tool.execute(&args, &ctx).await.unwrap();
        assert!(result.success, "Error: {:?}", result.error);
        assert!(file_path.exists());
    }

    #[tokio::test]
    async fn test_file_write_denied_path() {
        let tmpdir = TempDir::new().unwrap();
        let denied_dir = tmpdir.path().join("secret");
        std::fs::create_dir_all(&denied_dir).unwrap();
        let denied_pattern = format!("{}/**", denied_dir.to_string_lossy());
        let target = denied_dir.join("shadow.txt");

        let tool = FileWriteTool::new(FilesystemPermissions {
            read: vec![],
            write: vec![format!("{}/**", tmpdir.path().to_string_lossy())],
            deny: vec![denied_pattern],
        });
        let ctx = SecurityContext::new("test".into());
        let mut args = HashMap::new();
        args.insert("path".into(), Value::String(target.to_string_lossy().to_string()));
        args.insert("content".into(), Value::String("hacked".into()));

        let result = tool.execute(&args, &ctx).await.unwrap();
        assert!(!result.success);
        assert!(result.error.as_ref().unwrap().contains("denied"));
    }

    #[test]
    fn test_validate_args_missing_path() {
        let tool = FileWriteTool::new(FilesystemPermissions::default());
        let mut args = HashMap::new();
        args.insert("content".into(), Value::String("hello".into()));
        assert!(tool.validate_args(&args).is_err());
    }

    #[test]
    fn test_validate_args_missing_content() {
        let tool = FileWriteTool::new(FilesystemPermissions::default());
        let mut args = HashMap::new();
        args.insert("path".into(), Value::String("/tmp/test.txt".into()));
        assert!(tool.validate_args(&args).is_err());
    }

    #[test]
    fn test_tool_metadata() {
        let tool = FileWriteTool::new(FilesystemPermissions::default());
        assert_eq!(tool.name(), "file_write");
        assert_eq!(tool.risk_level(), RiskLevel::High);
        assert!(!tool.is_cacheable());
    }
}
