from __future__ import annotations

from datetime import datetime, timedelta, timezone
import random
import secrets
from typing import Protocol

from fastapi import HTTPException
from firebase_admin import firestore
from google.cloud.firestore_v1 import Client

from app.schemas.quiz import LeaderboardEntry, QuizAnswerResult, QuizLeaderboard, QuizQuestion, QuizSession


QUESTION_BANK = [
    ("epl_arsenal_invincibles", "Premier League", "Which club completed the 2003–04 Premier League season unbeaten?", ["Arsenal", "Chelsea", "Liverpool", "Manchester United"], 0, "Arsenal's Invincibles finished the league season without a defeat."),
    ("epl_top_scorer", "Premier League", "Who is the Premier League's all-time leading goalscorer?", ["Wayne Rooney", "Harry Kane", "Alan Shearer", "Thierry Henry"], 2, "Alan Shearer scored 260 Premier League goals."),
    ("epl_city_100", "Premier League", "Which club first reached 100 points in a Premier League season?", ["Liverpool", "Manchester City", "Chelsea", "Arsenal"], 1, "Manchester City earned 100 points in 2017–18."),
    ("laliga_titles", "LaLiga", "Which club has won the most LaLiga titles?", ["Barcelona", "Atlético Madrid", "Athletic Club", "Real Madrid"], 3, "Real Madrid holds the record for LaLiga championships."),
    ("laliga_messi", "LaLiga", "For which club did Lionel Messi score 474 LaLiga goals?", ["Barcelona", "Sevilla", "Valencia", "Real Madrid"], 0, "All 474 were scored for Barcelona."),
    ("laliga_derbi", "LaLiga", "El Derbi Madrileño is contested by Real Madrid and which club?", ["Getafe", "Atlético Madrid", "Rayo Vallecano", "Real Sociedad"], 1, "It is the major Madrid derby between Real and Atlético."),
    ("bundesliga_record", "Bundesliga", "Which club has won the most Bundesliga titles?", ["Borussia Dortmund", "Werder Bremen", "Bayern Munich", "Hamburg"], 2, "Bayern Munich is Germany's record champion."),
    ("bundesliga_50", "Bundesliga", "Which club became the first unbeaten Bundesliga champion in 2023–24?", ["Bayer Leverkusen", "Bayern Munich", "RB Leipzig", "Stuttgart"], 0, "Bayer Leverkusen completed the league season unbeaten."),
    ("bundesliga_revier", "Bundesliga", "Borussia Dortmund contests the Revierderby against which club?", ["Bochum", "Schalke 04", "Bayern Munich", "Köln"], 1, "Dortmund and Schalke contest the Revierderby."),
    ("ligue1_record", "Ligue 1", "Which club became the first to win 12 French top-flight titles?", ["Marseille", "Monaco", "Paris Saint-Germain", "Saint-Étienne"], 2, "PSG reached a record 12 titles in 2024."),
    ("ligue1_invincible", "Ligue 1", "Which club plays home matches at the Parc des Princes?", ["Lyon", "Lille", "Paris Saint-Germain", "Nice"], 2, "The Parc des Princes is PSG's home stadium."),
    ("seriea_record", "Serie A", "Which club has won the most Serie A championships?", ["AC Milan", "Inter Milan", "Roma", "Juventus"], 3, "Juventus is Italy's record league champion."),
    ("seriea_derby", "Serie A", "The Derby della Madonnina is played by Inter and which club?", ["Juventus", "AC Milan", "Atalanta", "Roma"], 1, "Inter and AC Milan share the famous Milan derby."),
    ("ucl_record", "Champions League", "Which club has won the most European Cup/Champions League titles?", ["AC Milan", "Bayern Munich", "Liverpool", "Real Madrid"], 3, "Real Madrid is the competition's record winner."),
    ("europa_record", "Europa League", "Which club has won the most UEFA Cup/Europa League titles?", ["Sevilla", "Liverpool", "Inter Milan", "Atlético Madrid"], 0, "Sevilla holds the competition record."),
    ("wc_most", "World Cup", "Which nation has won the most men's FIFA World Cups?", ["Germany", "Brazil", "Italy", "Argentina"], 1, "Brazil has won five men's World Cups."),
    ("wc_2022", "World Cup", "Who won the 2022 FIFA World Cup?", ["France", "Brazil", "Argentina", "Croatia"], 2, "Argentina defeated France in the final."),
    ("wc_first", "World Cup", "Which nation won the first FIFA World Cup in 1930?", ["Uruguay", "Argentina", "Italy", "Brazil"], 0, "Hosts Uruguay won the inaugural tournament."),
    ("wc_klose", "World Cup", "Who holds the men's World Cup record for career goals?", ["Ronaldo", "Miroslav Klose", "Gerd Müller", "Lionel Messi"], 1, "Miroslav Klose scored 16 World Cup goals."),
    ("player_cr7", "Players", "Which player is commonly known as CR7?", ["Cristiano Ronaldo", "Ronaldo Nazário", "Ronaldinho", "Roberto Carlos"], 0, "CR7 refers to Cristiano Ronaldo."),
    ("player_ballondor", "Players", "Which player won a record eighth Ballon d'Or in 2023?", ["Cristiano Ronaldo", "Karim Benzema", "Lionel Messi", "Luka Modrić"], 2, "Lionel Messi won his eighth award in 2023."),
    ("player_pele", "Players", "Pelé represented which country?", ["Portugal", "Brazil", "Argentina", "Colombia"], 1, "Pelé won three World Cups with Brazil."),
    ("player_zidane", "Players", "Zinedine Zidane represented which national team?", ["France", "Algeria", "Spain", "Italy"], 0, "Zidane was a World Cup winner with France."),
    ("rules_players", "Football Knowledge", "How many players does each team normally start with?", ["9", "10", "11", "12"], 2, "A team starts with eleven players."),
    ("rules_penalty", "Football Knowledge", "How far is the penalty spot from the goal line?", ["10 yards", "11 yards", "12 yards", "15 yards"], 2, "The penalty mark is 12 yards from the goal line."),
    ("club_anfield", "Clubs", "Which club plays at Anfield?", ["Everton", "Liverpool", "Manchester United", "Aston Villa"], 1, "Anfield is Liverpool's home ground."),
    ("club_bernabeu", "Clubs", "Which club plays at the Santiago Bernabéu?", ["Real Madrid", "Atlético Madrid", "Barcelona", "Sevilla"], 0, "The Bernabéu is Real Madrid's home stadium."),
    ("club_signal", "Clubs", "Signal Iduna Park is home to which club?", ["Bayern Munich", "Schalke 04", "Borussia Dortmund", "Bayer Leverkusen"], 2, "Borussia Dortmund plays at Signal Iduna Park."),
    ("flag_brazil", "Country Flags", "Which national team uses this flag? 🇧🇷", ["Brazil", "Portugal", "Mexico", "Colombia"], 0, "The green, yellow and blue flag belongs to Brazil."),
    ("flag_argentina", "Country Flags", "Name the country represented by this flag: 🇦🇷", ["Uruguay", "Argentina", "Paraguay", "Chile"], 1, "The sky-blue and white flag is Argentina's."),
    ("flag_france", "Country Flags", "Which football nation is represented by 🇫🇷?", ["Netherlands", "Croatia", "France", "Belgium"], 2, "This blue-white-red tricolour is France."),
    ("flag_germany", "Country Flags", "Identify this national-team flag: 🇩🇪", ["Belgium", "Germany", "Austria", "Spain"], 1, "Black, red and gold represent Germany."),
    ("flag_spain", "Country Flags", "Which country plays under this flag? 🇪🇸", ["Spain", "Portugal", "Romania", "Ecuador"], 0, "This is Spain's national flag."),
    ("flag_england", "Country Flags", "The Three Lions represent which country? 🏴", ["Scotland", "Wales", "England", "Northern Ireland"], 2, "The Three Lions are England's national team."),
    ("crest_cannon", "Club Symbols", "Which club's crest prominently features a cannon?", ["West Ham United", "Arsenal", "Aston Villa", "Chelsea"], 1, "Arsenal's identity and crest feature a cannon."),
    ("crest_liverbird", "Club Symbols", "The Liver bird is the symbol of which club?", ["Everton", "Liverpool", "Manchester City", "Newcastle United"], 1, "The Liver bird is central to Liverpool's crest."),
    ("crest_red_devil", "Club Symbols", "Which club is nicknamed the Red Devils and shows a devil on its crest?", ["Nottingham Forest", "Manchester United", "Bayern Munich", "AC Milan"], 1, "Manchester United is known as the Red Devils."),
    ("crest_bat", "Club Symbols", "Which Spanish club has a bat above the shield on its crest?", ["Valencia", "Villarreal", "Sevilla", "Real Betis"], 0, "Valencia's crest is topped by a bat."),
    ("crest_wolf", "Club Symbols", "Which Serie A club is represented by the she-wolf symbol?", ["Lazio", "Roma", "Torino", "Fiorentina"], 1, "Roma's crest depicts the Capitoline Wolf."),
    ("crest_fleur", "Club Symbols", "Which Ligue 1 club's modern crest features the Eiffel Tower?", ["Lyon", "Marseille", "Monaco", "Paris Saint-Germain"], 3, "PSG's crest prominently features the Eiffel Tower."),
]
QUESTION_BY_ID = {item[0]: item for item in QUESTION_BANK}


class QuizRepository(Protocol):
    def create(self, uid: str, question_ids: list[str], now: datetime) -> str: ...
    def answer(self, uid: str, session_id: str, question_id: str, answer_index: int, now: datetime) -> QuizAnswerResult: ...
    def leaderboard(self, uid: str, limit: int) -> QuizLeaderboard: ...


class FirestoreQuizRepository:
    def __init__(self, client: Client): self.client = client
    def create(self, uid, question_ids, now):
        session_id = secrets.token_urlsafe(18)
        self.client.collection("quiz_sessions").document(session_id).set({"userId": uid, "questionIds": question_ids, "currentQuestion": 0, "questionStartedAt": now, "createdAt": now, "completed": False})
        return session_id
    def answer(self, uid, session_id, question_id, answer_index, now):
        ref = self.client.collection("quiz_sessions").document(session_id); user_ref = self.client.collection("users").document(uid); tx = self.client.transaction()
        @firestore.transactional
        def apply(transaction):
            session = ref.get(transaction=transaction); user = user_ref.get(transaction=transaction)
            if not session.exists or session.to_dict().get("userId") != uid: raise HTTPException(404, "Quiz session not found")
            data = session.to_dict(); index = int(data.get("currentQuestion", 0)); ids = data["questionIds"]
            if data.get("completed") or index >= len(ids): raise HTTPException(409, "Quiz session is complete")
            if ids[index] != question_id: raise HTTPException(409, "Answer does not match the active question")
            started = data["questionStartedAt"]; timed_out = (now - started).total_seconds() > 15
            question = QUESTION_BY_ID[question_id]; correct = not timed_out and answer_index == question[4]; delta = 10 if correct else -5
            total = max(0, int((user.to_dict() if user.exists else {}).get("quizXp", 0)) + delta)
            next_index = index + 1; completed = next_index >= len(ids); next_started = None if completed else now
            transaction.set(user_ref, {"quizXp": total, "quizUpdatedAt": now}, merge=True)
            transaction.update(ref, {"currentQuestion": next_index, "questionStartedAt": next_started, "completed": completed, "updatedAt": now})
            return QuizAnswerResult(correct=correct, timedOut=timed_out, correctAnswerIndex=question[4], explanation=question[5], xpDelta=delta, totalXp=total, currentQuestion=next_index, completed=completed, questionStartedAt=next_started)
        return apply(tx)
    def leaderboard(self, uid, limit):
        docs = list(self.client.collection("users").order_by("quizXp", direction=firestore.Query.DESCENDING).limit(limit).stream())
        entries = [LeaderboardEntry(rank=i + 1, username=(d.to_dict().get("username") or "player"), xp=int(d.to_dict().get("quizXp", 0)), userId=d.id if d.id == uid else None) for i, d in enumerate(docs)]
        me = next((x for x in entries if x.userId == uid), None)
        return QuizLeaderboard(entries=entries, me=me)


class QuizService:
    def __init__(self, repository: QuizRepository): self.repository = repository
    def start(self, uid: str) -> QuizSession:
        now = datetime.now(timezone.utc); selected = random.SystemRandom().sample(QUESTION_BANK, 5); ids = [x[0] for x in selected]
        session_id = self.repository.create(uid, ids, now)
        questions = [QuizQuestion(id=x[0], category=x[1], prompt=x[2], options=x[3]) for x in selected]
        return QuizSession(sessionId=session_id, questions=questions, questionStartedAt=now, expiresAt=now + timedelta(seconds=75))
