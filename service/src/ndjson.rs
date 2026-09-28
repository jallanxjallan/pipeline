use crate::{
    ServiceError, ServiceResult,
    types::{PipelineCall, PlanId, ResponseAction},
};

pub fn validate_dispatch_calls(
    records: &[PipelineCall],
    plan: &PlanId,
    source_path: &str,
    action: ResponseAction,
) -> ServiceResult<()> {
    for record in records {
        if record.record_type != "call" {
            return Err(ServiceError::InvalidInput(format!(
                "{source_path}: call has type {:?}, expected \"call\"",
                record.record_type
            )));
        }
        if record.identity.trim().is_empty() {
            return Err(ServiceError::InvalidInput(format!(
                "{source_path}: call has an empty identity"
            )));
        }
        if record.content.trim().is_empty() {
            return Err(ServiceError::InvalidInput(format!(
                "{source_path}: document content is blank"
            )));
        }
        if record.plan != plan.0 {
            return Err(ServiceError::InvalidInput(format!(
                "{source_path}: call has plan {:?}, expected {:?}",
                record.plan, plan.0
            )));
        }
        if record.extra.source.kind != "vault" {
            return Err(ServiceError::InvalidInput(format!(
                "{source_path}: call source kind must be \"vault\""
            )));
        }
        if record.extra.source.path.as_deref() != Some(source_path) {
            return Err(ServiceError::InvalidInput(format!(
                "{source_path}: call source path {:?} does not match",
                record.extra.source.path
            )));
        }
        if record.extra.response.action != action {
            return Err(ServiceError::InvalidInput(format!(
                "{source_path}: call response action does not match request"
            )));
        }
    }
    Ok(())
}

pub fn serialize_calls(records: &[PipelineCall]) -> ServiceResult<Vec<u8>> {
    let mut output = Vec::new();
    for record in records {
        serde_json::to_writer(&mut output, record)
            .map_err(|error| ServiceError::InvalidInput(error.to_string()))?;
        output.push(b'\n');
    }
    Ok(output)
}
