use crate::{
    ServiceError, ServiceResult,
    selector::Record,
    types::{PlanId, PlanSummary},
};
use rusqlite::{Connection, Row};
use std::path::{Path, PathBuf};

#[derive(Debug, Clone)]
pub struct ControlDb {
    path: PathBuf,
}

impl ControlDb {
    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self { path: path.into() }
    }

    pub fn default_path() -> PathBuf {
        std::env::var_os("AUTOSCRIBE_CONTROL_DB")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("/home/jeremy/Data/control.sql"))
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    fn connect(&self) -> ServiceResult<Connection> {
        Connection::open(&self.path).map_err(|e| {
            ServiceError::Server(format!("open control db {}: {e}", self.path.display()))
        })
    }

    fn plan_columns(conn: &Connection) -> ServiceResult<Vec<String>> {
        let mut stmt = conn
            .prepare("PRAGMA table_info(plans)")
            .map_err(|e| ServiceError::Server(format!("inspect plans schema: {e}")))?;
        let rows = stmt
            .query_map([], |row| row.get::<_, String>(1))
            .map_err(|e| ServiceError::Server(format!("inspect plans schema: {e}")))?;
        let mut cols = Vec::new();
        for row in rows {
            cols.push(row.map_err(|e| ServiceError::Server(format!("inspect plans schema: {e}")))?);
        }
        if cols.is_empty() {
            return Err(ServiceError::Server("control db has no plans table".into()));
        }
        Ok(cols)
    }

    fn choose<'a>(cols: &'a [String], candidates: &[&str], what: &str) -> ServiceResult<&'a str> {
        for candidate in candidates {
            if let Some(found) = cols.iter().find(|c| c.as_str() == *candidate) {
                return Ok(found.as_str());
            }
        }
        Err(ServiceError::Server(format!(
            "plans table has no {what} column (expected one of: {})",
            candidates.join(", ")
        )))
    }

    pub fn plan_types(&self) -> ServiceResult<Vec<Record>> {
        let conn = self.connect()?;
        let cols = Self::plan_columns(&conn)?;
        let type_col = Self::choose(&cols, &["plan_type", "type", "kind"], "plan type")?;
        let sql = format!(
            r#"SELECT DISTINCT "{type_col}" FROM plans WHERE "{type_col}" IS NOT NULL AND trim("{type_col}") <> '' ORDER BY "{type_col}" COLLATE NOCASE"#
        );
        let mut stmt = conn
            .prepare(&sql)
            .map_err(|e| ServiceError::Server(format!("query plan types: {e}")))?;
        let rows = stmt
            .query_map([], |row| row.get::<_, String>(0))
            .map_err(|e| ServiceError::Server(format!("query plan types: {e}")))?;
        let mut out = Vec::new();
        for row in rows {
            let value = row.map_err(|e| ServiceError::Server(format!("query plan types: {e}")))?;
            out.push(Record {
                value: value.clone(),
                display: value,
            });
        }
        if out.is_empty() {
            return Err(ServiceError::InvalidInput(
                "control db contains no plan types".into(),
            ));
        }
        Ok(out)
    }

    pub fn plans_by_type(&self, plan_type: &str) -> ServiceResult<Vec<PlanSummary>> {
        let conn = self.connect()?;
        let cols = Self::plan_columns(&conn)?;
        let id_col = Self::choose(&cols, &["identity", "slug", "id"], "plan identity")?;
        let label_col = Self::choose(&cols, &["label", "title", "name"], "plan label")?;
        let type_col = Self::choose(&cols, &["plan_type", "type", "kind"], "plan type")?;
        let sql = format!(
            r#"SELECT "{id_col}", "{label_col}" FROM plans WHERE "{type_col}" = ?1 ORDER BY "{label_col}" COLLATE NOCASE, "{id_col}""#
        );
        let mut stmt = conn
            .prepare(&sql)
            .map_err(|e| ServiceError::Server(format!("query plans: {e}")))?;
        let rows = stmt
            .query_map([plan_type], |row: &Row<'_>| {
                Ok(PlanSummary {
                    slug: row.get(0)?,
                    label: row.get(1)?,
                })
            })
            .map_err(|e| ServiceError::Server(format!("query plans: {e}")))?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row.map_err(|e| ServiceError::Server(format!("query plans: {e}")))?);
        }
        if out.is_empty() {
            return Err(ServiceError::InvalidInput(format!(
                "no plans of type {plan_type}"
            )));
        }
        Ok(out)
    }

    pub fn require_plan(&self, plan: &PlanId) -> ServiceResult<PlanSummary> {
        let conn = self.connect()?;
        let cols = Self::plan_columns(&conn)?;
        let id_col = Self::choose(&cols, &["identity", "slug", "id"], "plan identity")?;
        let label_col = Self::choose(&cols, &["label", "title", "name"], "plan label")?;
        let sql =
            format!(r#"SELECT "{id_col}", "{label_col}" FROM plans WHERE "{id_col}" = ?1 LIMIT 1"#);
        conn.query_row(&sql, [&plan.0], |row| {
            Ok(PlanSummary {
                slug: row.get(0)?,
                label: row.get(1)?,
            })
        })
        .map_err(|e| match e {
            rusqlite::Error::QueryReturnedNoRows => {
                ServiceError::InvalidInput(format!("plan {} is not in the control db", plan.0))
            }
            other => ServiceError::Server(format!("query plan {}: {other}", plan.0)),
        })
    }
}

impl Default for ControlDb {
    fn default() -> Self {
        Self::new(Self::default_path())
    }
}
