"""Authentication/authorization primitives shared by server, write service and retrieval."""
from hlmemo.auth.context import AuthContext, Role

__all__ = ["AuthContext", "Role"]
