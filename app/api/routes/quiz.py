from fastapi import APIRouter, Depends, Path, Query
from app.api.dependencies import CurrentUser, get_current_user, get_quiz_repository
from app.schemas.quiz import QuizAnswerRequest, QuizAnswerResult, QuizLeaderboard, QuizSession
from app.services.quiz import QuizRepository, QuizService

router = APIRouter(prefix="/quiz", tags=["quiz"])

@router.post("/sessions", response_model=QuizSession)
def start_quiz(user: CurrentUser = Depends(get_current_user), repository: QuizRepository = Depends(get_quiz_repository)):
    return QuizService(repository).start(user.uid)

@router.post("/sessions/{session_id}/answer", response_model=QuizAnswerResult)
def answer_quiz(payload: QuizAnswerRequest, session_id: str = Path(max_length=80), user: CurrentUser = Depends(get_current_user), repository: QuizRepository = Depends(get_quiz_repository)):
    from datetime import datetime, timezone
    return repository.answer(user.uid, session_id, payload.questionId, payload.answerIndex, datetime.now(timezone.utc))

@router.get("/leaderboard", response_model=QuizLeaderboard)
def leaderboard(limit: int = Query(20, ge=1, le=100), user: CurrentUser = Depends(get_current_user), repository: QuizRepository = Depends(get_quiz_repository)):
    return repository.leaderboard(user.uid, limit)
