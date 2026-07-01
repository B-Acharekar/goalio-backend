from datetime import datetime
from pydantic import BaseModel, Field


class QuizQuestion(BaseModel):
    id: str
    category: str
    prompt: str
    options: list[str]
    timeLimitSeconds: int = 15


class QuizSession(BaseModel):
    sessionId: str
    questions: list[QuizQuestion]
    currentQuestion: int = 0
    questionStartedAt: datetime
    expiresAt: datetime


class QuizAnswerRequest(BaseModel):
    questionId: str
    answerIndex: int = Field(ge=-1, le=3)


class QuizAnswerResult(BaseModel):
    correct: bool
    timedOut: bool
    correctAnswerIndex: int
    explanation: str
    xpDelta: int
    totalXp: int
    currentQuestion: int
    completed: bool
    questionStartedAt: datetime | None = None


class LeaderboardEntry(BaseModel):
    rank: int
    username: str
    xp: int
    userId: str | None = None


class QuizLeaderboard(BaseModel):
    entries: list[LeaderboardEntry]
    me: LeaderboardEntry | None = None
