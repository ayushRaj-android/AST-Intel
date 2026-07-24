from fastapi import APIRouter, FastAPI

app = FastAPI()
router = APIRouter()


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/users")
async def create_user():
    return {"id": 1}


@router.get("/api/users/{user_id}")
async def get_user(user_id: int):
    return {"id": user_id}


@router.delete("/api/users/{user_id}")
async def delete_user(user_id: int):
    return {"deleted": True}


def helper_function():
    """This has no route decorator."""
    pass
