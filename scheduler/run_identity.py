"""Fail-closed execution identity shared by scheduled and webhook flows."""


async def require_execution_account(db, flow: dict) -> str:
    email = flow.get("run_as")
    if not isinstance(email, str) or "@" not in email:
        raise ValueError("Execution blocked: select and save an active execution account.")
    email = email.strip().lower()
    user = await db.users.find_one({"email": email}, {"status": 1})
    if not user or user.get("status") != "active":
        raise ValueError("Execution blocked: execution account is missing or inactive. Ask an admin to update it.")
    return email
