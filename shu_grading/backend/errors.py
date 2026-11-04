class ServiceError(Exception):
    """可安全展示给调用者的业务错误，不包含模型密钥或内部异常。"""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class GradingFailure(Exception):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class LeaseLost(Exception):
    pass
