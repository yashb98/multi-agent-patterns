"""Tests for jobpulse/github_matcher.py — Task 6: GitHub Matcher."""

import pytest
from jobpulse.github_matcher import score_repo, pick_top_projects

MOCK_REPOS = [
    {
        "name": "Velox_AI",
        "description": "Enterprise AI Voice Agent Platform",
        "languages": ["python", "javascript"],
        "topics": ["ai", "voice", "fastapi", "docker", "gcp"],
        "keywords": ["python", "fastapi", "docker", "gcp", "langchain", "websocket", "ai", "voice", "real-time", "kubernetes"],
    },
    {
        "name": "Cloud-Sentinel",
        "description": "AI Powered Cloud Security Platform",
        "languages": ["python", "typescript"],
        "topics": ["security", "rag", "react", "fastapi", "docker"],
        "keywords": ["python", "react", "fastapi", "docker", "redis", "pinecone", "rag", "embeddings", "security", "mcp", "typescript"],
    },
    {
        "name": "90-Days-ML",
        "description": "90 Days Machine Learning journey",
        "languages": ["python"],
        "topics": ["machine-learning", "pytorch", "tensorflow", "scikit-learn"],
        "keywords": ["python", "pytorch", "tensorflow", "scikit-learn", "pandas", "numpy", "matplotlib", "machine learning", "deep learning", "eda", "mlflow", "mlops"],
    },
    {
        "name": "3D-Face-Reconstruction",
        "description": "Deep Learning for Facial 3D Reconstructions",
        "languages": ["python"],
        "topics": ["deep-learning", "pytorch", "computer-vision"],
        "keywords": ["python", "pytorch", "computer vision", "deep learning", "3d reconstruction", "ssim", "cnn"],
    },
]


def test_score_repo_data_science():
    """ML-heavy JD should rank 90-Days-ML highest."""
    jd_required = ["python", "sql", "machine learning", "scikit-learn", "pandas"]
    jd_preferred = ["pytorch", "tensorflow", "mlflow"]
    scores = {repo["name"]: score_repo(repo, jd_required, jd_preferred) for repo in MOCK_REPOS}
    assert scores["90-Days-ML"] > scores["Velox_AI"]
    assert scores["90-Days-ML"] > scores["Cloud-Sentinel"]


def test_score_repo_cloud_engineering():
    """Cloud/infra JD should rank Velox AI or Cloud Sentinel highest."""
    jd_required = ["python", "docker", "kubernetes", "fastapi", "gcp"]
    jd_preferred = ["redis", "ci/cd"]
    scores = {repo["name"]: score_repo(repo, jd_required, jd_preferred) for repo in MOCK_REPOS}
    assert scores["Velox_AI"] > scores["90-Days-ML"]
    assert scores["Cloud-Sentinel"] > scores["90-Days-ML"]


def test_pick_top_projects():
    """pick_top_projects returns 3 repos sorted by score."""
    jd_required = ["python", "pytorch", "deep learning"]
    jd_preferred = ["computer vision"]
    top = pick_top_projects(MOCK_REPOS, jd_required, jd_preferred, top_n=3)
    assert len(top) == 3
    assert top[0]["name"] in ("90-Days-ML", "3D-Face-Reconstruction")


def test_pick_top_projects_limit_4():
    """Can request top 4."""
    top = pick_top_projects(MOCK_REPOS, ["python"], [], top_n=4)
    assert len(top) == 4
