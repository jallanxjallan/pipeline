use crate::{ServiceError, ServiceResult, markdown};
use serde_json::Value;
use std::{
    fs,
    io::{BufRead, Write},
    path::{Path, PathBuf},
};

pub fn run(input: impl BufRead, target_dir: Option<&Path>) -> ServiceResult<()> {
    let target = target_dir.unwrap_or_else(|| Path::new("_ingest"));
    let target = expand_home(target)?;
    let target = fs::canonicalize(&target).map_err(|error| {
        ServiceError::Io(format!("Target directory {}: {error}", target.display()))
    })?;
    if !target.is_dir() {
        return Err(ServiceError::InvalidInput(format!(
            "Target path is not a directory: {}",
            target.display()
        )));
    }

    // Parse the entire stream before materialising any records, as in Python.
    let mut records = Vec::new();
    for (line_number, line) in input.lines().enumerate() {
        let line = line.map_err(|error| {
            ServiceError::Io(format!("stdin line {}: {error}", line_number + 1))
        })?;
        if line.trim().is_empty() {
            continue;
        }
        let record: Value = serde_json::from_str(&line).map_err(|error| {
            ServiceError::InvalidInput(format!(
                "Record {} (line {}): {error}",
                records.len() + 1,
                line_number + 1
            ))
        })?;
        records.push(record);
    }
    for (index, record) in records.iter().enumerate() {
        write_record(record, &target).map_err(|error| {
            let message = format!("Record {}: {error}", index + 1);
            match error {
                ServiceError::InvalidInput(_) => ServiceError::InvalidInput(message),
                ServiceError::Io(_) => ServiceError::Io(message),
                _ => ServiceError::Process(message),
            }
        })?;
    }
    Ok(())
}

fn expand_home(path: &Path) -> ServiceResult<PathBuf> {
    if let Ok(rest) = path.strip_prefix("~") {
        let home = std::env::var_os("HOME")
            .ok_or_else(|| ServiceError::InvalidInput("Cannot expand ~: HOME is not set".into()))?;
        return Ok(PathBuf::from(home).join(rest));
    }
    Ok(path.to_path_buf())
}

fn nonempty<'a>(value: &'a Value, field: &str) -> ServiceResult<&'a str> {
    value
        .as_str()
        .map(str::trim)
        .filter(|text| !text.is_empty())
        .ok_or_else(|| {
            ServiceError::InvalidInput(format!("Expected {field} to be a non-empty string"))
        })
}

fn slug_hint(slug: &str) -> String {
    if let Some(part) = slug
        .split('.')
        .nth(1)
        .filter(|part| !part.trim().is_empty())
    {
        let words: Vec<String> = part
            .split('-')
            .filter(|word| !word.is_empty())
            .map(|word| {
                let mut chars = word.chars();
                let first = chars.next().unwrap().to_uppercase().collect::<String>();
                first + &chars.as_str().to_lowercase()
            })
            .collect();
        if !words.is_empty() {
            return words.join(" ");
        }
    }
    slug.to_owned()
}

fn write_record(record: &Value, target: &Path) -> ServiceResult<()> {
    if !record.is_object() {
        return Err(ServiceError::InvalidInput(
            "Record must be an object".into(),
        ));
    }
    let metadata = &record["input_record"];
    if !metadata.is_object() {
        return Err(ServiceError::InvalidInput(
            "Expected input_record to be an object".into(),
        ));
    }
    let slug = nonempty(&metadata["slug"], "input_record.slug")?;
    let hint = if metadata["filename_hint"].is_null() {
        slug_hint(slug)
    } else {
        nonempty(&metadata["filename_hint"], "input_record.filename_hint")?.to_owned()
    };
    // Match pathlib.Path(hint).name: directories are discarded, not created.
    let filename = Path::new(&hint)
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("")
        .trim();
    if filename.is_empty() || matches!(filename, "." | "..") {
        return Err(ServiceError::InvalidInput(format!(
            "Invalid filename hint: {hint}"
        )));
    }
    let filename = if filename.ends_with(".md") {
        filename.to_owned()
    } else {
        format!("{filename}.md")
    };
    let destination = target.join(filename);
    // Reject even dangling symlinks; the writer must never follow an existing entry.
    match fs::symlink_metadata(&destination) {
        Ok(_) => {
            return Err(ServiceError::InvalidInput(format!(
                "Destination already exists: {}",
                destination.display()
            )));
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => {
            return Err(ServiceError::Io(format!(
                "{}: {error}",
                destination.display()
            )));
        }
    }
    if destination.parent() != Some(target) {
        return Err(ServiceError::InvalidInput(format!(
            "Destination escapes target directory: {}",
            destination.display()
        )));
    }
    let content = nonempty(&record["content"], "content")?;
    let (_, body) = markdown::split_frontmatter(content)?;
    let mut rendered = String::from("---\n");
    for (key, value) in metadata.as_object().unwrap() {
        if value.is_array() || value.is_object() {
            return Err(ServiceError::InvalidInput(format!(
                "input_record.{key} must be scalar"
            )));
        }
        rendered.push_str(&serde_json::to_string(key).unwrap());
        rendered.push_str(": ");
        rendered.push_str(&serde_json::to_string(value).unwrap());
        rendered.push('\n');
    }
    rendered.push_str("---\n");
    rendered.push_str(body);
    if !rendered.ends_with('\n') {
        rendered.push('\n');
    }
    let mut file = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&destination)
        .map_err(|error| ServiceError::Io(format!("{}: {error}", destination.display())))?;
    file.write_all(rendered.as_bytes())
        .map_err(|error| ServiceError::Io(format!("{}: {error}", destination.display())))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn native_frontmatter_body_and_destination_safety() {
        let target = std::env::temp_dir().join(format!(
            "writenew-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir(&target).unwrap();
        let record = json!({"input_record": {"slug": "pss.my-note", "filename_hint": "elsewhere/Note", "title": "A: \"title\"", "count": 2, "published": false}, "content": "---\nslug: wrong\n---\n# Body\n\n**Keep** Markdown."});
        run(std::io::Cursor::new(format!("{record}\n")), Some(&target)).unwrap();
        let expected = "---\n\"count\": 2\n\"filename_hint\": \"elsewhere/Note\"\n\"published\": false\n\"slug\": \"pss.my-note\"\n\"title\": \"A: \\\"title\\\"\"\n---\n# Body\n\n**Keep** Markdown.\n";
        assert_eq!(
            fs::read_to_string(target.join("Note.md")).unwrap(),
            expected
        );
        assert!(
            write_record(&record, &target)
                .unwrap_err()
                .to_string()
                .contains("already exists")
        );
        assert_eq!(
            fs::read_to_string(target.join("Note.md")).unwrap(),
            expected
        );
        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(target.join("missing"), target.join("Link.md")).unwrap();
            let linked = json!({"input_record":{"slug":"pss.link", "filename_hint":"Link"},"content":"Body"});
            assert!(
                write_record(&linked, &target)
                    .unwrap_err()
                    .to_string()
                    .contains("already exists")
            );
            assert!(!target.join("missing").exists());
        }
        for invalid in [
            json!({}),
            json!({"input_record":{"slug":" "},"content":"Body"}),
            json!({"input_record":{"slug":"pss.blank"},"content":" "}),
            json!({"input_record":{"slug":"pss.nested", "nested":{}},"content":"Body"}),
        ] {
            assert!(write_record(&invalid, &target).is_err());
        }
        fs::remove_dir_all(target).unwrap();
    }
}
