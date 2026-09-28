use autoscribe_service::{ServiceError, ServiceResult, asc::AscClient, snapshot};
use std::{env, io::Write, path::PathBuf, process::ExitCode};

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("inflight: {error}");
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
    let mut vault = env::current_dir()
        .map_err(|error| ServiceError::Io(format!("current directory: {error}")))?;
    let args: Vec<_> = env::args().skip(1).collect();
    let mut index = 0usize;
    while index < args.len() {
        match args[index].as_str() {
            "--vault" => {
                index += 1;
                vault =
                    PathBuf::from(args.get(index).ok_or_else(|| {
                        ServiceError::InvalidInput("--vault requires a path".into())
                    })?);
            }
            value => {
                return Err(ServiceError::InvalidInput(format!(
                    "unknown inflight option: {value}"
                )));
            }
        }
        index += 1;
    }

    let repo = snapshot::root(&vault)?;
    let state = asc_client().dispatch_state(&repo)?;
    let mut output = serde_json::to_vec_pretty(&state).map_err(|error| {
        ServiceError::InvalidInput(format!("serialize inflight state: {error}"))
    })?;
    output.push(b'\n');
    std::io::stdout()
        .write_all(&output)
        .map_err(|error| ServiceError::Io(format!("stdout: {error}")))
}
