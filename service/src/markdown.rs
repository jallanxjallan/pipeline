use crate::{ServiceError, ServiceResult};

/// Split only a leading YAML envelope, retaining its exact bytes, including delimiters.
pub fn split_frontmatter(text: &str) -> ServiceResult<(Option<&str>, &str)> {
    let mut lines = text.split_inclusive('\n');
    let Some(first) = lines.next() else {
        return Ok((None, text));
    };
    if first.trim_end_matches(['\r', '\n']) != "---" {
        return Ok((None, text));
    }
    let mut offset = first.len();
    for line in lines {
        offset += line.len();
        if line.trim_end_matches(['\r', '\n']) == "---" {
            return Ok((Some(&text[..offset]), &text[offset..]));
        }
    }
    Err(ServiceError::InvalidInput(
        "unterminated leading YAML frontmatter".into(),
    ))
}

/// Only the leading, standalone `::: directive` / `::: {.directive}` block.
/// This is deliberately not a general fenced-div or Markdown parser.
pub fn extract_directive(body: &str) -> ServiceResult<(Option<String>, &str)> {
    let leading = body.trim_start_matches([' ', '\t', '\r', '\n']);
    let mut lines = leading.split_inclusive('\n');
    let Some(first) = lines.next() else {
        return Ok((None, body));
    };
    if !matches!(
        first.trim_end_matches(['\r', '\n']),
        "::: directive" | "::: {.directive}"
    ) {
        return Ok((None, body));
    }
    let start = first.len();
    let mut offset = start;
    for line in lines {
        if line.trim_end_matches(['\r', '\n']) == ":::" {
            let directive = leading[start..offset].trim();
            if directive.is_empty() {
                return Err(ServiceError::InvalidInput(
                    "leading directive is blank".into(),
                ));
            }
            return Ok((Some(directive.to_owned()), &leading[offset + line.len()..]));
        }
        offset += line.len();
    }
    Err(ServiceError::InvalidInput(
        "unterminated leading directive".into(),
    ))
}

pub(crate) fn metadata(prefix: Option<&str>) -> ServiceResult<serde_yaml::Value> {
    let prefix =
        prefix.ok_or_else(|| ServiceError::InvalidInput("source has no frontmatter".into()))?;
    let mut lines = prefix.lines();
    lines.next();
    let mut lines: Vec<_> = lines.collect();
    lines.pop();
    serde_yaml::from_str(&lines.join("\n"))
        .map_err(|error| ServiceError::InvalidInput(format!("frontmatter: {error}")))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn exact_frontmatter_and_body() {
        let prefix = "---\r\nslug: 'pss.test' # keep\r\ncustom: [a, b]\r\n---\r\n";
        let text = format!("{prefix}Body\n---\nLater\n");
        assert_eq!(
            split_frontmatter(&text).unwrap(),
            (Some(prefix), "Body\n---\nLater\n")
        );
        assert_eq!(
            split_frontmatter("Body\n---\n").unwrap(),
            (None, "Body\n---\n")
        );
        assert_eq!(split_frontmatter("").unwrap(), (None, ""));
        assert!(split_frontmatter("---\nslug: x\n").is_err());
        assert!(split_frontmatter("---").is_err());
    }
    #[test]
    fn narrow_directives() {
        for opener in ["::: directive", "::: {.directive}"] {
            let text = format!("\n{opener}\nFix **this**.\n:::\n\nBody\n");
            assert_eq!(
                extract_directive(&text).unwrap(),
                (Some("Fix **this**.".into()), "\nBody\n")
            );
        }
        for body in [
            "ordinary\n::: directive\nStay\n:::\n",
            "::: note\nStay\n:::\n",
        ] {
            assert_eq!(extract_directive(body).unwrap(), (None, body));
        }
        assert!(extract_directive("::: directive\n \n:::\n").is_err());
        assert!(extract_directive("::: directive\nMissing close").is_err());
    }
}
