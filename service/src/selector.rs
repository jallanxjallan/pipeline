//! Domain-independent single selection. Values never come from display text.
use crate::{ServiceError, ServiceResult};
use skim::prelude::*;
use std::io::IsTerminal;

#[derive(Clone, Debug)]
pub struct Record {
    pub value: String,
    pub display: String,
}

impl SkimItem for Record {
    fn text(&self) -> Cow<'_, str> {
        Cow::Borrowed(&self.display)
    }
    fn output(&self) -> Cow<'_, str> {
        Cow::Borrowed(&self.value)
    }
}

pub fn select_one(prompt: &str, records: Vec<Record>) -> ServiceResult<Option<String>> {
    if records.is_empty() {
        return Err(ServiceError::InvalidInput(format!(
            "{prompt}: no candidates"
        )));
    }
    if !std::io::stdin().is_terminal() || !std::io::stderr().is_terminal() {
        return Err(ServiceError::InvalidInput(
            "interactive selection requires a terminal; supply --repo, --commit, and --plan".into(),
        ));
    }
    let options = SkimOptionsBuilder::default()
        .prompt(prompt)
        .height("60%")
        .multi(false)
        .no_sort(true)
        .build()
        .map_err(|error| ServiceError::Process(format!("selector options: {error}")))?;
    let (sender, receiver): (SkimItemSender, SkimItemReceiver) = unbounded();
    let items: Vec<Arc<dyn SkimItem>> = records
        .into_iter()
        .map(|record| Arc::new(record) as Arc<dyn SkimItem>)
        .collect();
    sender
        .send(items)
        .map_err(|error| ServiceError::Process(format!("selector items: {error}")))?;
    drop(sender);
    let output = Skim::run_with(options, Some(receiver))
        .map_err(|error| ServiceError::Process(format!("selector: {error}")))?;
    if output.is_abort {
        return Ok(None);
    }
    Ok(output
        .selected_items
        .first()
        .map(|item| item.output().into_owned()))
}
