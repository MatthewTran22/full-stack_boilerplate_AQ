from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routes.clone import router as clone_router

app = FastAPI(title="Website Cloner API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(clone_router)


@app.get("/")
def root():
    return {"message": "Website Cloner API is running"}
