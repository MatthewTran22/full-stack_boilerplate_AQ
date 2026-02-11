import os
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    datefmt="%H:%M:%S",
)
# Quiet noisy HTTP logs unless debugging
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routes.clone import router as clone_router

app = FastAPI(title="Website Cloner API")

allowed_origins = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:5050").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(clone_router)


@app.get("/")
def root():
    return {"message": "Website Cloner API is running"}


@app.get("/health")
def health():
    return {"status": "ok"}
