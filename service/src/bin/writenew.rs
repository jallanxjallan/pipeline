use autoscribe_service::{ServiceError, ServiceResult, operations::writenew};
use std::{env, path::PathBuf, process::ExitCode};

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("writenew: {error}");
            ExitCode::FAILURE
        }
    }
}

fn run() -> ServiceResult<()> {
    let mut target_dir = None;
    let args: Vec<_> = env::args().skip(1).collect();
    let mut index = 0usize;
    while index < args.len() {
        match args[index].as_str() {
            "--target-dir" => {
                index += 1;
                target_dir = Some(PathBuf::from(args.get(index).ok_or_else(|| {
                    ServiceError::InvalidInput("--target-dir requires a path".into())
                })?));
            }
            value => {
                return Err(ServiceError::InvalidInput(format!(
                    "unknown writenew option: {value}"
                )));
            }
        }
        index += 1;
    }
    writenew::run(std::io::stdin().lock(), target_dir.as_deref())
}
