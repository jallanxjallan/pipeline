use autoscribe_service::{
    ndjson,
    types::{PipelineCall, ResponseAction},
};

#[test]
fn dispatch_ndjson_requires_explicit_routing() {
    let bytes = br#"{"type":"call","identity":"psg.test","content":"Body","plan":"plan.test","extra":{"source":{"kind":"vault","path":"Content/Test.md","status":"ai-process"},"response":{"action":"writeback"},"assets":[],"context":{}}}
"#;
    let records: Vec<PipelineCall> = vec![serde_json::from_slice(bytes).unwrap()];
    ndjson::validate_dispatch_calls(
        &records,
        &autoscribe_service::types::PlanId("plan.test".into()),
        "Content/Test.md",
        ResponseAction::Writeback,
    )
    .unwrap();
    let reparsed: Vec<PipelineCall> =
        vec![serde_json::from_slice(&ndjson::serialize_calls(&records).unwrap()).unwrap()];
    assert_eq!(records, reparsed);
}

#[test]
fn validates_routing_and_preserves_compatibility_action() {
    use autoscribe_service::types::PlanId;
    let valid: PipelineCall = serde_json::from_value(serde_json::json!({
        "type":"call", "identity":"pss.test", "content":"Body", "plan":"plan.test",
        "extra":{"source":{"kind":"vault","path":"Note.md","status":"ai-process"},"response":{"action":"export"}}
    })).unwrap();
    let validate = |call: &PipelineCall| {
        ndjson::validate_dispatch_calls(
            std::slice::from_ref(call),
            &PlanId("plan.test".into()),
            "Note.md",
            ResponseAction::Export,
        )
    };
    validate(&valid).unwrap();
    let serialized = serde_json::to_value(&valid).unwrap();
    assert_eq!(serialized["extra"]["response"]["action"], "export");
    assert!(serialized.get("directive").is_none());
    for field in ["type", "identity", "content", "plan"] {
        let mut value = serialized.clone();
        value[field] = serde_json::Value::String(String::new());
        assert!(validate(&serde_json::from_value(value).unwrap()).is_err());
    }
    for field in ["kind", "path"] {
        let mut value = serialized.clone();
        value["extra"]["source"][field] = serde_json::Value::String("wrong".into());
        assert!(validate(&serde_json::from_value(value).unwrap()).is_err());
    }
    let mut legacy_status = serialized.clone();
    legacy_status["extra"]["source"]["status"] = serde_json::Value::String("legacy".into());
    validate(&serde_json::from_value(legacy_status).unwrap()).unwrap();

    let mut wrong_action = valid;
    wrong_action.extra.response.action = ResponseAction::None;
    assert!(validate(&wrong_action).is_err());
}
