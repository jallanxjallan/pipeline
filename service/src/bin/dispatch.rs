use autoscribe_service::{
    ServiceError, ServiceResult,
    asc::AscClient,
    control_db::ControlDb,
    operations::dispatch,
    selector, snapshot,
    types::{PlanId, ResponseAction},
};
use std::{env, io::Write, path::PathBuf, process::ExitCode, str::FromStr};

fn main() -> ExitCode {
    match run(env::args().skip(1).collect()) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("dispatch: {error}");
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

fn run(args: Vec<String>) -> ServiceResult<()> {
    let request = parse_args(&args)?;
    let asc = asc_client();
    let control = ControlDb::default();

    let repo = match request.repo {
        Some(repo) => snapshot::root(&repo)?,
        None => {
            let records = snapshot::repositories(&snapshot::default_repositories_root())?;
            let Some(repo) = selector::select_one("Repository> ", records)? else {
                return Ok(());
            };
            PathBuf::from(repo)
        }
    };

    let commit = match request.commit {
        Some(commit) => snapshot::full_commit(&repo, &commit)?,
        None => {
            let state = asc.dispatch_state(&repo)?;
            let records = snapshot::commits(&repo)?
                .into_iter()
                .filter(|record| {
                    !state.queued_commits.contains(&record.value)
                        && !state.inflight_commits.contains(&record.value)
                })
                .take(100)
                .collect();
            let Some(commit) = selector::select_one("Unqueued commit> ", records)? else {
                return Ok(());
            };
            commit
        }
    };

    let plan_type = match request.plan_type {
        Some(plan_type) => plan_type,
        None if request.plan.is_some() => String::new(),
        None => {
            let Some(plan_type) = selector::select_one("Plan type> ", control.plan_types()?)?
            else {
                return Ok(());
            };
            plan_type
        }
    };

    let plan = match request.plan {
        Some(plan) => {
            control.require_plan(&PlanId(plan.clone()))?;
            plan
        }
        None => {
            let records = control
                .plans_by_type(&plan_type)?
                .into_iter()
                .map(|plan| selector::Record {
                    value: plan.slug,
                    display: plan.label,
                })
                .collect();
            let Some(plan) = selector::select_one("Plan> ", records)? else {
                return Ok(());
            };
            plan
        }
    };

    let response = dispatch::run_commit(
        &repo,
        &commit,
        &PlanId(plan),
        request.action,
        &request.sources,
        &asc,
    )?;
    std::io::stdout()
        .write_all(&response)
        .map_err(|error| ServiceError::Io(format!("stdout: {error}")))
}

struct Options {
    repo: Option<PathBuf>,
    commit: Option<String>,
    plan_type: Option<String>,
    plan: Option<String>,
    action: ResponseAction,
    sources: Vec<PathBuf>,
}

fn parse_args(args: &[String]) -> ServiceResult<Options> {
    let mut repo = None;
    let mut plan_type = None;
    let mut plan = None;
    let mut commit = None;
    let mut action = ResponseAction::Writeback;
    let mut sources = Vec::new();
    let mut index = 0;

    while index < args.len() {
        match args[index].as_str() {
            "--repo" => {
                index += 1;
                repo = Some(PathBuf::from(args.get(index).ok_or_else(|| {
                    ServiceError::InvalidInput("--repo requires a path".into())
                })?));
            }
            "--plan-type" => {
                index += 1;
                plan_type = Some(
                    args.get(index)
                        .ok_or_else(|| {
                            ServiceError::InvalidInput("--plan-type requires a value".into())
                        })?
                        .clone(),
                );
            }
            "--commit" => {
                index += 1;
                commit = Some(
                    args.get(index)
                        .ok_or_else(|| {
                            ServiceError::InvalidInput("--commit requires a full SHA".into())
                        })?
                        .clone(),
                );
            }
            "--plan" => {
                index += 1;
                plan = args.get(index).cloned();
                if plan.is_none() {
                    return Err(ServiceError::InvalidInput(
                        "--plan requires a plan slug".into(),
                    ));
                }
            }
            "--response-action" => {
                index += 1;
                let value = args.get(index).ok_or_else(|| {
                    ServiceError::InvalidInput("--response-action requires a value".into())
                })?;
                action = ResponseAction::from_str(value).map_err(ServiceError::InvalidInput)?;
            }
            "--help" | "-h" => {
                print!("{}", usage());
                std::process::exit(0);
            }
            value if value.starts_with('-') => {
                return Err(ServiceError::InvalidInput(format!(
                    "unknown dispatch option: {value}\n{}",
                    usage()
                )));
            }
            value => sources.push(PathBuf::from(value)),
        }
        index += 1;
    }

    Ok(Options {
        repo,
        commit,
        plan_type,
        plan,
        action,
        sources,
    })
}

fn usage() -> &'static str {
    "usage: dispatch [--repo <path>] [--commit <full-sha>] [--plan-type <type>] [--plan <plan-ref>] [--response-action writeback|writenew|export|none] [source.md ...]\n"
}
