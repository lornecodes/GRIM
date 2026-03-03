//! Shell tool — executes commands via the sandbox backend.

use anyhow::{bail, Result};
use async_trait::async_trait;
use serde_json::Value;
use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;
use tracing::info;

use crate::core::tool::Tool;
use crate::core::types::{RiskLevel, SecurityContext, ToolResult, ToolResultMetadata};
use crate::sandbox::SandboxBackend;

/// Default shell command timeout (30 seconds).
const DEFAULT_TIMEOUT_SECS: u64 = 30;

/// Maximum allowed timeout (10 minutes).
const MAX_TIMEOUT_SECS: u64 = 600;

/// Shell tool — executes a command through the sandbox backend.
pub struct ShellTool {
    sandbox: Arc<dyn SandboxBackend>,
}

impl ShellTool {
    pub fn new(sandbox: Arc<dyn SandboxBackend>) -> Self {
        Self { sandbox }
    }
}

#[async_trait]
impl Tool for ShellTool {
    fn name(&self) -> &str {
        "shell"
    }

    fn description(&self) -> &str {
        "Execute a shell command in the sandbox. Returns stdout, stderr, and exit code."
    }

    fn parameters_schema(&self) -> Value {
        serde_json::json!({
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute"
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 30, max 600)"
                }
            },
            "required": ["command"]
        })
    }

    fn risk_level(&self) -> RiskLevel {
        RiskLevel::Critical
    }

    fn required_permissions(&self) -> Vec<String> {
        vec!["shell.execute".to_string()]
    }

    fn validate_args(&self, args: &HashMap<String, Value>) -> Result<()> {
        let command = args
            .get("command")
            .and_then(|v| v.as_str())
            .ok_or_else(|| anyhow::anyhow!("Missing required argument: command"))?;

        if command.is_empty() {
            bail!("Command cannot be empty");
        }

        if let Some(timeout) = args.get("timeout").and_then(|v| v.as_u64()) {
            if timeout > MAX_TIMEOUT_SECS {
                bail!(
                    "Timeout {} exceeds maximum of {} seconds",
                    timeout,
                    MAX_TIMEOUT_SECS
                );
            }
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

        let command = args
            .get("command")
            .and_then(|v| v.as_str())
            .unwrap_or_default();

        let timeout_secs = args
            .get("timeout")
            .and_then(|v| v.as_u64())
            .unwrap_or(DEFAULT_TIMEOUT_SECS)
            .min(MAX_TIMEOUT_SECS);

        let timeout = Duration::from_secs(timeout_secs);

        info!(
            command = %command,
            timeout_secs = %timeout_secs,
            sandbox = %self.sandbox.name(),
            "shell executing"
        );

        let sandbox_result = self
            .sandbox
            .execute(command, &HashMap::new(), timeout)
            .await?;

        let duration_ms = start.elapsed().as_millis() as u64;

        // Combine stdout and stderr for output
        let mut output = sandbox_result.stdout.clone();
        if !sandbox_result.stderr.is_empty() {
            if !output.is_empty() {
                output.push('\n');
            }
            output.push_str("[stderr] ");
            output.push_str(&sandbox_result.stderr);
        }

        if sandbox_result.timed_out {
            output.push_str("\n[Command timed out]");
        }

        let success = sandbox_result.exit_code == 0 && !sandbox_result.timed_out;

        let error = if !success {
            if sandbox_result.timed_out {
                Some(format!(
                    "Command timed out after {} seconds",
                    timeout_secs
                ))
            } else {
                Some(format!(
                    "Command exited with code {}",
                    sandbox_result.exit_code
                ))
            }
        } else {
            None
        };

        info!(
            command = %command,
            exit_code = sandbox_result.exit_code,
            timed_out = sandbox_result.timed_out,
            duration_ms = duration_ms,
            "shell completed"
        );

        Ok(ToolResult {
            success,
            output,
            error,
            metadata: ToolResultMetadata {
                tool_name: "shell".into(),
                duration_ms,
                sandboxed: true,
                risk_level: RiskLevel::Critical,
                execution_id: String::new(),
                started_at,
                completed_at: chrono::Utc::now(),
                exit_code: Some(sandbox_result.exit_code),
                bytes_read: 0,
                bytes_written: 0,
                truncated: false,
                provider_usage: None,
            },
        })
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sandbox::{ResourceUsage, SandboxResult as SbResult};

    /// Mock sandbox backend for testing.
    struct MockSandbox {
        result: parking_lot::Mutex<Option<SbResult>>,
    }

    impl MockSandbox {
        fn new(result: SbResult) -> Self {
            Self {
                result: parking_lot::Mutex::new(Some(result)),
            }
        }

        fn success(stdout: &str) -> Self {
            Self::new(SbResult {
                exit_code: 0,
                stdout: stdout.to_string(),
                stderr: String::new(),
                timed_out: false,
                resource_usage: ResourceUsage::default(),
            })
        }

        fn failure(exit_code: i32, stderr: &str) -> Self {
            Self::new(SbResult {
                exit_code,
                stdout: String::new(),
                stderr: stderr.to_string(),
                timed_out: false,
                resource_usage: ResourceUsage::default(),
            })
        }

        fn timeout() -> Self {
            Self::new(SbResult {
                exit_code: -1,
                stdout: String::new(),
                stderr: "Execution timed out".to_string(),
                timed_out: true,
                resource_usage: ResourceUsage::default(),
            })
        }
    }

    #[async_trait]
    impl SandboxBackend for MockSandbox {
        async fn execute(
            &self,
            _command: &str,
            _env: &HashMap<String, String>,
            _timeout: Duration,
        ) -> Result<SbResult> {
            Ok(self
                .result
                .lock()
                .take()
                .unwrap_or_else(|| SbResult {
                    exit_code: 0,
                    stdout: String::new(),
                    stderr: String::new(),
                    timed_out: false,
                    resource_usage: ResourceUsage::default(),
                }))
        }

        async fn is_available(&self) -> bool {
            true
        }

        fn name(&self) -> &str {
            "mock"
        }
    }

    #[tokio::test]
    async fn test_shell_echo() {
        let sandbox = Arc::new(MockSandbox::success("hello world"));
        let tool = ShellTool::new(sandbox);
        let ctx = SecurityContext::new("test".into());
        let mut args = HashMap::new();
        args.insert("command".into(), Value::String("echo hello world".into()));

        let result = tool.execute(&args, &ctx).await.unwrap();
        assert!(result.success);
        assert_eq!(result.output, "hello world");
        assert!(result.metadata.sandboxed);
        assert_eq!(result.metadata.exit_code, Some(0));
    }

    #[tokio::test]
    async fn test_shell_failure() {
        let sandbox = Arc::new(MockSandbox::failure(1, "not found"));
        let tool = ShellTool::new(sandbox);
        let ctx = SecurityContext::new("test".into());
        let mut args = HashMap::new();
        args.insert("command".into(), Value::String("false".into()));

        let result = tool.execute(&args, &ctx).await.unwrap();
        assert!(!result.success);
        assert!(result.output.contains("[stderr] not found"));
        assert_eq!(result.metadata.exit_code, Some(1));
        assert!(result.error.as_ref().unwrap().contains("code 1"));
    }

    #[tokio::test]
    async fn test_shell_timeout() {
        let sandbox = Arc::new(MockSandbox::timeout());
        let tool = ShellTool::new(sandbox);
        let ctx = SecurityContext::new("test".into());
        let mut args = HashMap::new();
        args.insert("command".into(), Value::String("sleep 1000".into()));

        let result = tool.execute(&args, &ctx).await.unwrap();
        assert!(!result.success);
        assert!(result.output.contains("[Command timed out]"));
        assert!(result.error.as_ref().unwrap().contains("timed out"));
    }

    #[test]
    fn test_validate_args_missing_command() {
        let sandbox = Arc::new(MockSandbox::success(""));
        let tool = ShellTool::new(sandbox);
        let args = HashMap::new();
        assert!(tool.validate_args(&args).is_err());
    }

    #[test]
    fn test_validate_args_empty_command() {
        let sandbox = Arc::new(MockSandbox::success(""));
        let tool = ShellTool::new(sandbox);
        let mut args = HashMap::new();
        args.insert("command".into(), Value::String(String::new()));
        assert!(tool.validate_args(&args).is_err());
    }

    #[test]
    fn test_validate_args_timeout_too_large() {
        let sandbox = Arc::new(MockSandbox::success(""));
        let tool = ShellTool::new(sandbox);
        let mut args = HashMap::new();
        args.insert("command".into(), Value::String("echo hi".into()));
        args.insert("timeout".into(), Value::Number(9999.into()));
        assert!(tool.validate_args(&args).is_err());
    }

    #[test]
    fn test_validate_args_valid_timeout() {
        let sandbox = Arc::new(MockSandbox::success(""));
        let tool = ShellTool::new(sandbox);
        let mut args = HashMap::new();
        args.insert("command".into(), Value::String("echo hi".into()));
        args.insert("timeout".into(), Value::Number(60.into()));
        assert!(tool.validate_args(&args).is_ok());
    }

    #[test]
    fn test_tool_metadata() {
        let sandbox = Arc::new(MockSandbox::success(""));
        let tool = ShellTool::new(sandbox);
        assert_eq!(tool.name(), "shell");
        assert_eq!(tool.risk_level(), RiskLevel::Critical);
        assert!(!tool.is_cacheable());
    }
}
