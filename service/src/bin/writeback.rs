use autoscribe_service::{ServiceError, ServiceResult, asc::AscClient, operations::writeback};
use std::{env, io::Write, path::PathBuf, process::ExitCode};

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("writeback: {error}");
            ExitCode::FAILURE
        }
    }
}

fn asc_client() -> AscClient {
    AscClient::new(
        env::var_os("AUTOSCRIBE_ASC")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("asc")),
    )
}

fn run() -> ServiceResult<()> {
    let args: Vec<_> = env::args().skip(1).collect();
    if !args.is_empty() {
        return Err(ServiceError::InvalidInput(
            "writeback takes no arguments; $PWD itself must be the Git repository root".into(),
        ));
    }
    let cwd = env::current_dir()
        .map_err(|error| ServiceError::Io(format!("current directory: {error}")))?;
    let report = writeback::run(&cwd, &asc_client())?;
    std::io::stdout()
        .write_all(&report)
        .map_err(|error| ServiceError::Io(format!("stdout: {error}")))?;
    let value: serde_json::Value = serde_json::from_slice(&report)
        .map_err(|error| ServiceError::InvalidInput(error.to_string()))?;
    if value["receipt_failures"]
        .as_array()
        .is_some_and(|failures| !failures.is_empty())
    {
        return Err(ServiceError::Server(
            "writeback has failed receipts; see JSON report; rerun to recover from Git trailers"
                .into(),
        ));
    }
    Ok(())
}
