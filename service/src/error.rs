use std::fmt::{Display, Formatter};

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ServiceError {
    InvalidInput(String),
    Io(String),
    Process(String),
    Server(String),
}

impl Display for ServiceError {
    fn fmt(&self, f: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::InvalidInput(message) => write!(f, "invalid input: {message}"),
            Self::Io(message) => write!(f, "I/O error: {message}"),
            Self::Process(message) => write!(f, "process error: {message}"),
            Self::Server(message) => write!(f, "server error: {message}"),
        }
    }
}

impl std::error::Error for ServiceError {}

pub type ServiceResult<T> = Result<T, ServiceError>;
