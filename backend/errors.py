class AppError(Exception):
    def __init__(self, code: str, message: str, status: int = 422, retryable: bool = False):
        self.code, self.message, self.status, self.retryable = code, message, status, retryable
        super().__init__(message)

    def public(self):
        return {"code": self.code, "message": self.message, "retryable": self.retryable}
