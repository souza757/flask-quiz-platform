from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path
from typing import Any, Optional

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
)



BASE_DIR = Path(__file__).parent


QUESTIONS_FILE = BASE_DIR / "questions.json"


HIGHSCORES_FILE = BASE_DIR / "highscores.json"


NUM_QUESTIONS_PER_QUIZ = 5


QUESTION_TIME_LIMIT = 20  # 

DIFFICULTY_WEIGHTS = {
    "easy": 1,
    "medium": 2,
    "hard": 3,
}


app = Flask(__name__)


app.secret_key = "change-me-to-something-secret"

#this function reads the questions json file and checks everything inside it to make sure each question has answers and valid correct indexes and a difficulty and then it cleans everything up into a dictionary the rest of the app can use while throwing errors if the data is messed up so the quiz doesn’t break later

def load_questions(json_path: Path) -> dict[str, dict[str, Any]]:

    with json_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("Questions JSON must be an object mapping questions to entries")

    normalized: dict[str, dict[str, Any]] = {}
    for q, value in data.items():
        if not isinstance(q, str):
            raise ValueError("Each question key must be a string")

        # treat the first answer as the correct one
        if isinstance(value, list):
            if not value:
                raise ValueError("Answer list cannot be empty")
            normalized[q] = {
                "answers": value,
                "correct_indices": [0],
                "explanation": "",
                "difficulty": "easy",
            }
            continue

        
        if not isinstance(value, dict):
            raise ValueError(
                "Each question value must be a list of answers or "
                "an object with 'answers' and optional 'explanation'"
            )

        answers = value.get("answers")
        if not isinstance(answers, list) or not answers:
            raise ValueError("Each question entry must include a non-empty 'answers' list")

        correct_indices = value.get("correct_indices", [0])
        if not isinstance(correct_indices, list) or not all(isinstance(i, int) for i in correct_indices):
            raise ValueError("'correct_indices' must be a list of integers if provided")

        
        n = len(answers)
        for i in correct_indices:
            if i < 0 or i >= n:
                raise ValueError("'correct_indices' contains an index out of range for answers list")

        explanation = value.get("explanation", "")
        if explanation is not None and not isinstance(explanation, str):
            raise ValueError("'explanation' must be a string if provided")

        difficulty = value.get("difficulty", "easy")
        if not isinstance(difficulty, str):
            difficulty = "easy"

        normalized[q] = {
            "answers": answers,
            "correct_indices": correct_indices,
            "explanation": explanation or "",
            "difficulty": difficulty.lower(),
        }

    return normalized


#this function randomly picks a certain number of questions from the question bank but never more than what actually exists so every quiz gets a fresh shuffled set of questions
def select_questions(
    questions: dict[str, dict[str, Any]],
    num_per_quiz: int,
) -> list[tuple[str, dict[str, Any]]]:

    num_to_select = min(num_per_quiz, len(questions))
    return random.sample(list(questions.items()), num_to_select)



#this loads the highscores file if it exists and if it doesn’t or it’s messed up it just gives back an empty dict so the game doesn’t break
def load_highscores(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

#this just takes all the scores we have and saves them into the file so they don’t disappear
def save_highscores(path: Path, all_scores: dict) -> None:
    path.write_text(json.dumps(all_scores, indent=2), encoding="utf-8")


#this checks if the new score is actually better than the old one by comparing percent first then how many they got right if percent is the same
def better_than(current: dict, best: Optional[dict]) -> bool:
    if best is None:
        return True
    curr_pct = current.get("best_percent", 0.0)
    best_pct = best.get("best_percent", 0.0)
    if curr_pct > best_pct:
        return True
    if abs(curr_pct - best_pct) < 1e-9 and current.get("num_correct", 0) > best.get("num_correct", 0):
        return True
    return False

# this makes a little ranking value using percent and number correct so we can sort players from best to worst
def champ_key(rec: dict) -> tuple:
    return (rec.get("best_percent", 0.0), rec.get("num_correct", 0))

#this looks through every player score and finds the best overall player using the ranking rules and returns their name and record.
def compute_champion(all_scores: dict) -> tuple[Optional[str], Optional[dict]]:
    champion_user = None
    champion_record = None
    for user, rec in all_scores.items():
        if champion_record is None or champ_key(rec) > champ_key(champion_record):
            champion_user = user
            champion_record = rec
    return champion_user, champion_record

#this turns all saved scores into a sorted list of top players and includes stats like percent correct and points so we can show a clean leaderboard
def build_leaderboard(all_scores: dict, top_n: int = 10) -> list[dict]:
    rows = []
    for user, rec in all_scores.items():
        pct = rec.get("best_percent", 0.0) * 100
        rows.append(
            {
                "user": user,
                "percent": pct,
                "num_correct": rec.get("num_correct", 0),
                "num_asked": rec.get("num_asked", 0),
                "difficulty": rec.get("difficulty", "easy"),
                "points": rec.get("points", rec.get("num_correct", 0)),
                "seconds_taken": rec.get("seconds_taken", 0.0),
            }
        )
    rows.sort(key=lambda r: (r["percent"], r["num_correct"]), reverse=True)
    return rows[:top_n]


# ------------------- Flask routes -------------------

@app.route("/", methods=["GET"])

#this route shows the home page where you can start a new quiz and also loads and displays the current champion and leaderboard while clearing out any old quiz data in the session
def index():
    all_scores = load_highscores(HIGHSCORES_FILE)
    champion_user, champion_rec = compute_champion(all_scores)
    champion_info = None
    if champion_user and champion_rec:
        champion_info = {
            "user": champion_user,
            "percent": champion_rec.get("best_percent", 0.0) * 100,
            "num_correct": champion_rec.get("num_correct", 0),
            "num_asked": champion_rec.get("num_asked", 0),
        }

    leaderboard = build_leaderboard(all_scores)
    # Clear any old quiz from session when going home
    session.pop("quiz", None)
    return render_template(
        "index.html",
        champion=champion_info,
        leaderboard=leaderboard,
        default_num_questions=NUM_QUESTIONS_PER_QUIZ,
    )


@app.route("/start", methods=["POST"])
#R4: this is a big part of requirement 4 because it reads the chosen difficulty from the form filters questions by that difficulty and also uses difficulty later for scoring
# this route handles the start quiz form reads the name difficulty and number of questions sets up a new quiz with random questions and shuffled answers then stores everything in the session and sends the user to the quiz page
def start_quiz():
    username = (request.form.get("username") or "").strip()
    difficulty = (request.form.get("difficulty") or "easy").lower()
    try:
        num_questions = int(request.form.get("num_questions", NUM_QUESTIONS_PER_QUIZ))
    except ValueError:
        num_questions = NUM_QUESTIONS_PER_QUIZ

    if not username:
        flash("Please enter your name before starting.")
        return redirect(url_for("index"))

    try:
        bank = load_questions(QUESTIONS_FILE)
    except FileNotFoundError:
        flash("Server error: questions.json not found.")
        return redirect(url_for("index"))
    except ValueError as e:
        flash(f"Server error: invalid questions file: {e}")
        return redirect(url_for("index"))

        # R4: only keep questions whose difficulty matches what the user chose so easy/medium/hard actually change the quiz content
    filtered = {
        q: info for q, info in bank.items()
        if info.get("difficulty", "easy").lower() == difficulty
    }
    if not filtered:
        #R4: fallback to all questions so the quiz still runs even if that difficulty has no questions
        filtered = bank

    selected = select_questions(filtered, num_questions)

    quiz_questions: list[dict[str, Any]] = []
    for question_text, info in selected:
        answers = info.get("answers", [])
        correct_indices_orig = info.get("correct_indices", [0])

        # Shuffle answer options 
        indexed = list(enumerate(answers))
        random.shuffle(indexed)
        new_answers: list[str] = [t[1] for t in indexed]
        new_correct_indices: list[int] = [
            i for i, (orig_idx, _) in enumerate(indexed)
            if orig_idx in correct_indices_orig
        ]

        quiz_questions.append(
            {
                "question": question_text,
                "answers": new_answers,
                "correct_indices": new_correct_indices,
                "explanation": info.get("explanation", ""),
            }
        )

    if not quiz_questions:
        flash("No questions available for this quiz.")
        return redirect(url_for("index"))

    now = time.time()
    session["quiz"] = {
        "username": username,
        "difficulty": difficulty, # R4: store difficulty in the session so later scoring and summary know which level this run used
        "num_questions": len(quiz_questions),
        "questions": quiz_questions,
        "current_index": 0,
        "num_correct": 0,
        "num_incorrect": 0,
        "num_timed_out": 0,
        "history": [],
        "start_time": now,
        "question_start_time": now, # R4: this timestamp plus QUESTION_TIME_LIMIT is what enforces a time limit per question

    }

    return redirect(url_for("quiz"))


@app.route("/quiz", methods=["GET", "POST"])
#R7: this is the main place for requirement 7 because it figures out if an answer is correct or not and builds the feedback data the template uses to color answers green or red
#this route either shows the current question with a timer (on get) or checks the submitted answer and time limit (on post) updates the quiz stats saves feedback moves to the next question and either shows a feedback screen or sends the user to the summary when the quiz is done
def quiz():
    quiz = session.get("quiz")
    if not quiz:
        flash("Your quiz session expired or has not started yet.")
        return redirect(url_for("index"))

    now = time.time()


    if request.method == "POST":
        idx = quiz["current_index"]
        if idx >= quiz["num_questions"]:
            return redirect(url_for("summary"))

        question = quiz["questions"][idx]
        elapsed = now - quiz["question_start_time"]
        timed_out = elapsed > QUESTION_TIME_LIMIT # R4: this line actually enforces the time limit per question for difficulty and counts late answers as timed out


        selected_str = request.form.get("answer")
        selected_index: Optional[int] = None
        if selected_str is not None and selected_str != "":
            try:
                selected_index = int(selected_str)
            except ValueError:
                selected_index = None # R7: this tracks which option the user actually clicked so we can highlight it


        correct_indices = question["correct_indices"]
        is_correct = False

        if timed_out:
            quiz["num_timed_out"] += 1
        else:
            if selected_index is not None and selected_index in correct_indices:
                is_correct = True # R7: this flag says the user picked the right answer so the ui can show a green style

                quiz["num_correct"] += 1
            else:
                quiz["num_incorrect"] += 1 # R7: this path means the answer was wrong so the ui can show red and the correct one
        feedback = {
            "question": question["question"],
            "answers": question["answers"],
            "correct_indices": correct_indices, # R7: the template uses this list to know which answers to mark as correct visually
            "selected_index": selected_index,  # R7: this is the specific choice the user picked so we can show that one as their selection
            "timed_out": timed_out, # R4 + R7: lets the ui show special feedback when the question was missed because the timer ran out
            "is_correct": is_correct, # R7: single boolean that drives green vs red styling for the answer feedback
            "explanation": question.get("explanation", ""),
        }
        quiz["history"].append(feedback) # R7: keeps a record so we could later use it for more detailed feedback per question

        quiz["current_index"] += 1
        quiz["question_start_time"] = now # R4: reset the timer for the next question so each one gets its own countdown
        session["quiz"] = quiz

        is_last = quiz["current_index"] >= quiz["num_questions"]
        return render_template(
            "quiz.html",
            mode="feedback",
            quiz=quiz,
            feedback=feedback, # R7: sent to quiz.html so css classes can use correct_indices and selected_index for red/green visual effects
            is_last=is_last,
            time_limit=QUESTION_TIME_LIMIT,
        )

    idx = quiz["current_index"]
    if idx >= quiz["num_questions"]:
        return redirect(url_for("summary"))

    question = quiz["questions"][idx]
    time_left = max(0, int(QUESTION_TIME_LIMIT - (now - quiz["question_start_time"]))) # R4: this value lets the ui show how many seconds remain to answer

    return render_template(
        "quiz.html",
        mode="question",
        quiz=quiz,
        question=question,
        time_left=time_left,
        time_limit=QUESTION_TIME_LIMIT,
    )


@app.route("/summary", methods=["GET"])
#R4: this helps requirement 4 because it uses the difficulty level to weight the score so hard mode gives more points and also shows feedback about performance at the end
#this route pulls the finished quiz from the session calculates final stats like percent correct time taken and points updates highscores if this run is a new personal best figures out the champion rebuilds the leaderboard clears the session quiz and shows the final results page
def summary():
    quiz = session.get("quiz")
    if not quiz:
        flash("No finished quiz found.")
        return redirect(url_for("index"))

    username = quiz["username"]
    difficulty = quiz["difficulty"] # R4: pull difficulty back out so we know which level this run used
    num_questions = quiz["num_questions"]
    num_correct = quiz["num_correct"]
    num_incorrect = quiz["num_incorrect"]
    num_timed_out = quiz["num_timed_out"]
    start_time = quiz["start_time"]

    total_elapsed = time.time() - start_time
    best_percent = (num_correct / num_questions) if num_questions else 0.0
    weight = DIFFICULTY_WEIGHTS.get(difficulty, 1) # R4: different difficulty levels map to different weights here easy 1 medium 2 hard 3
    points = num_correct * weight # R4: this line makes hard questions worth more points so difficulty actually changes the scoring

    current_record = {
        "user": username,
        "difficulty": difficulty, # R4: store difficulty along with the record so the leaderboard and teacher can see what level they played on
        "best_percent": best_percent,
        "num_correct": num_correct,
        "num_asked": num_questions,
        "num_incorrect": num_incorrect,
        "num_timed_out": num_timed_out,
        "seconds_taken": total_elapsed,
        "points": points, # R4: this is the final difficulty adjusted score the app uses on the leaderboard
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    all_scores = load_highscores(HIGHSCORES_FILE)
    user_best = all_scores.get(username)
    new_personal_best = False
    if better_than(current_record, user_best):
        all_scores[username] = current_record
        save_highscores(HIGHSCORES_FILE, all_scores)
        new_personal_best = True

    champion_user, champion_rec = compute_champion(all_scores)
    champion_info = None
    if champion_user and champion_rec:
        champion_info = {
            "user": champion_user,
            "percent": champion_rec.get("best_percent", 0.0) * 100,
            "num_correct": champion_rec.get("num_correct", 0),
            "num_asked": champion_rec.get("num_asked", 0),
        }

    leaderboard = build_leaderboard(all_scores)

    session.pop("quiz", None)

    return render_template(
        "quiz.html",
        mode="summary",
        summary={
            "username": username,
            "difficulty": difficulty,
            "num_questions": num_questions,
            "num_correct": num_correct,
            "num_incorrect": num_incorrect,
            "num_timed_out": num_timed_out,
            "seconds_taken": total_elapsed,
            "best_percent": best_percent * 100,
            "points": points,
            "new_personal_best": new_personal_best,
        },
        champion=champion_info,
        leaderboard=leaderboard,
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(debug=True, port=port)


"""
Testing summary for the quiz web app

I tested the basic quiz flow by starting a quiz from the home page
entering a username picking a difficulty choosing a number of questions
answering them and checking that the summary page showed the number correct
number incorrect and total time

I tested difficulty levels (requirement 4) by running quizzes on easy medium and hard
and checking that the questions matched the difficulty in questions.json
and that the final points changed based on difficulty using the weights
easy 1 medium 2 hard 3

I tested the time limit part of requirement 4 by answering some questions quickly
and letting other questions sit until the timer ran out
and I checked that timed out questions were counted separately
and showed up in the summary as timed out

I tested the visual feedback (requirement 7) by picking correct and incorrect answers
and checking that the feedback screen highlighted the correct choice differently
than the wrong choice and showed the explanation text when it existed in questions.json

I tested input validation by trying to start a quiz with no username
and confirmed that the app showed a friendly error message
and did not start the quiz until I entered a name

I tested highscores and champion logic by playing multiple times with the same username
first getting a low score and then a higher score
and I checked that only the better score was saved and that the champion and leaderboard
updated correctly

I tested the max 6 questions fix by trying to set the number of questions higher than 6
in the home page form and confirming the browser would not go past 6
and then by sending a larger number through the request and checking that the backend
still only selected up to the actual number of questions using select_questions
so the quiz never tried to use more than 6 questions
"""
