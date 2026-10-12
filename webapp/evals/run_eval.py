#!/usr/bin/env python3
"""
Eval runner for the football chatbot.

Loads 28 questions from questions.jsonl, drives the chatbot through each,
grades the answers (deterministic for numeric/string, LLM judge for refusal),
and reports accuracy by category and failure type.

Usage:
    uv run --directory webapp python evals/run_eval.py --label baseline [--runs 1]
    uv run --directory webapp python evals/run_eval.py --label fixed --runs 3
"""

import json
import sys
import os
import argparse
import re
from pathlib import Path
from datetime import datetime
from collections import defaultdict
import traceback

sys.path.insert(0, str(Path(__file__).parent.parent))

from chatbot import Chatbot

try:
    import anthropic
except ImportError:
    anthropic = None


def load_questions(jsonl_path):
    """Load questions from JSONL file."""
    questions = []
    with open(jsonl_path) as f:
        for line in f:
            if line.strip():
                questions.append(json.loads(line))
    return questions


def extract_number(text):
    """Extract first number from text."""
    match = re.search(r'-?\d+(?:\.\d+)?', str(text))
    if match:
        try:
            if '.' in match.group():
                return float(match.group())
            return int(match.group())
        except ValueError:
            return None
    return None


def extract_string(text):
    """Extract likely string answer (normalize)."""
    text = str(text).strip()
    # Remove common phrases
    text = re.sub(r'(the |is |was |is the |are the )', '', text, flags=re.IGNORECASE)
    text = re.sub(r'[.,!?]$', '', text)
    return text.strip()


def grade_numeric(expected, answer, tolerance=0.05):
    """Grade numeric answer with tolerance."""
    if expected is None:
        return answer is None or answer == 0 or answer == "0"

    got = extract_number(answer)
    if got is None:
        return False

    # Absolute tolerance for small numbers, relative for large
    if expected == 0:
        return got == 0

    diff = abs(got - expected)
    threshold = max(tolerance * abs(expected), 1)
    return diff <= threshold


def grade_exact_string(expected, answer):
    """Grade string answer (case-insensitive substring match)."""
    if expected is None:
        return answer is None or str(answer).strip() == ""

    # For string matching, search for the expected value in the answer
    expected_clean = str(expected).lower().strip()
    answer_clean = str(answer).lower()
    answer_clean = answer_clean.replace("**", "").replace("__", "").replace("_", "")

    # Check if expected appears anywhere in answer (as substring or word)
    if expected_clean in answer_clean:
        return True

    # Also try word boundary match
    pattern = r'\b' + re.escape(expected_clean) + r'\b'
    return bool(re.search(pattern, answer_clean))


def grade_refusal(answer):
    """Check if answer is a refusal (says it lacks data)."""
    refusal_phrases = [
        "don't have",
        "no data",
        "not available",
        "not in",
        "unable to",
        "cannot",
        "don't track",
        "i lack",
        "not recorded",
        "no information",
    ]
    answer_lower = str(answer).lower()
    return any(phrase in answer_lower for phrase in refusal_phrases)


def grade_answer(question, answer):
    """Grade a single answer. Returns (correct, failure_type)."""
    match_type = question.get("match")
    expected = question.get("expected")

    try:
        if match_type == "numeric":
            correct = grade_numeric(expected, answer)
            failure = "wrong_value" if not correct else None
            return correct, failure

        elif match_type == "exact_string":
            correct = grade_exact_string(expected, answer)
            failure = "wrong_value" if not correct else None
            return correct, failure

        elif match_type == "refusal":
            correct = grade_refusal(answer)
            failure = "bad_refusal" if not correct else None
            return correct, failure

        else:
            return False, "unknown_match_type"

    except Exception as e:
        return False, f"grade_error: {str(e)[:50]}"


def run_question(chatbot, question):
    """Run one question through the chatbot. Returns (answer, tool_calls, error)."""
    try:
        chatbot.clear_history()
        answer = chatbot.chat(question["question"])

        # Count tool calls from the message history
        tool_calls = sum(
            1 for msg in chatbot.messages
            if isinstance(msg.get("content"), list)
            for block in msg["content"]
            if isinstance(block, dict) and block.get("type") == "tool_use"
        )

        return answer, tool_calls, None

    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)[:100]}"
        return None, 0, error_msg


def judge_refusal(question_text, answer, client):
    """Use LLM judge to score a refusal answer."""
    if not client:
        return None

    prompt = f"""You are grading a chatbot's answer to a fantasy football question.

Question: {question_text}

Chatbot's answer: {answer}

Is this a reasonable refusal? The chatbot should say it lacks data, not invent stats.
Respond with exactly one word: YES or NO.
"""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip().upper()
        return text.startswith("YES")
    except Exception as e:
        print(f"    [judge error: {str(e)[:50]}]", file=sys.stderr)
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="baseline", help="Label for this run (baseline, fixed, etc)")
    parser.add_argument("--runs", type=int, default=1, help="Number of times to run each question")
    parser.add_argument("--ids", help="Comma-separated question IDs to run (all if omitted)")
    args = parser.parse_args()

    # Load questions
    questions_path = Path(__file__).parent / "questions.jsonl"
    if not questions_path.exists():
        print(f"Error: {questions_path} not found")
        sys.exit(1)

    questions = load_questions(questions_path)

    # Filter by IDs if requested
    if args.ids:
        id_set = set(args.ids.split(","))
        questions = [q for q in questions if q["id"] in id_set]

    print(f"Running {len(questions)} questions × {args.runs} run(s) = {len(questions) * args.runs} total")
    print()

    # Initialize judge client (optional)
    judge_client = None
    if os.getenv("ANTHROPIC_API_KEY"):
        try:
            judge_client = anthropic.Anthropic()
        except Exception:
            pass

    # Run questions
    results = []
    for q_idx, question in enumerate(questions, 1):
        q_id = question["id"]
        q_text = question["question"]
        category = question.get("category", "unknown")

        print(f"[{q_idx:2d}/{len(questions)}] {q_id} ({category})")

        # Multiple runs
        verdicts_per_run = []

        for run_num in range(args.runs):
            try:
                chatbot = Chatbot()
                answer, tool_calls, error = run_question(chatbot, question)

                if error:
                    # Crash or error
                    verdict = {"correct": False, "failure": "crash", "answer": None, "error": error, "tool_calls": 0}
                    print(f"  Run {run_num + 1}: CRASH - {error[:60]}")

                elif answer is None:
                    verdict = {"correct": False, "failure": "crash", "answer": None, "error": "no answer", "tool_calls": tool_calls}
                    print(f"  Run {run_num + 1}: NO ANSWER")

                else:
                    correct, failure = grade_answer(question, answer)

                    # For refusal, use judge if uncertain
                    if question.get("match") == "refusal" and not correct and judge_client:
                        judge_result = judge_refusal(q_text, answer, judge_client)
                        if judge_result:
                            correct = True
                            failure = None

                    verdict = {
                        "correct": correct,
                        "failure": failure,
                        "answer": answer[:100] if isinstance(answer, str) else str(answer)[:100],
                        "error": None,
                        "tool_calls": tool_calls,
                    }

                    status = "✓" if correct else f"✗ ({failure})"
                    print(f"  Run {run_num + 1}: {status}")

                verdicts_per_run.append(verdict)

            except Exception as e:
                verdict = {
                    "correct": False,
                    "failure": "crash",
                    "answer": None,
                    "error": f"{type(e).__name__}: {str(e)[:80]}",
                    "tool_calls": 0,
                }
                print(f"  Run {run_num + 1}: CRASH - {type(e).__name__}")
                verdicts_per_run.append(verdict)

        # Aggregate across runs (all must be correct for final verdict)
        final_verdict = all(v["correct"] for v in verdicts_per_run)
        failure_types = [v["failure"] for v in verdicts_per_run if v["failure"]]
        most_common_failure = max(set(failure_types), key=failure_types.count) if failure_types else None

        results.append({
            "id": q_id,
            "category": category,
            "question": q_text,
            "expected": question.get("expected"),
            "correct": final_verdict,
            "failure": most_common_failure,
            "verdicts": verdicts_per_run,
        })

        print()

    # Aggregate results
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)

    total = len(results)
    correct = sum(1 for r in results if r["correct"])
    accuracy = correct / total * 100 if total > 0 else 0

    print(f"\nOverall: {correct}/{total} correct ({accuracy:.1f}%)")

    # By category
    print("\nBy Category:")
    by_category = defaultdict(lambda: {"correct": 0, "total": 0})
    for r in results:
        cat = r["category"]
        by_category[cat]["total"] += 1
        if r["correct"]:
            by_category[cat]["correct"] += 1

    for cat in sorted(by_category.keys()):
        stats = by_category[cat]
        cat_acc = stats["correct"] / stats["total"] * 100 if stats["total"] > 0 else 0
        print(f"  {cat:20s}: {stats['correct']:2d}/{stats['total']:2d} ({cat_acc:5.1f}%)")

    # By failure type
    print("\nFailures by Type:")
    by_failure = defaultdict(int)
    for r in results:
        if r["failure"]:
            by_failure[r["failure"]] += 1

    if by_failure:
        for failure_type in sorted(by_failure.keys()):
            print(f"  {failure_type:25s}: {by_failure[failure_type]:2d}")
    else:
        print("  (none)")

    # Save results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_dir = Path(__file__).parent.parent.parent / "results"
    results_dir.mkdir(exist_ok=True)

    results_file = results_dir / f"{args.label}_{timestamp}.json"
    with open(results_file, "w") as f:
        json.dump(
            {
                "label": args.label,
                "timestamp": timestamp,
                "total": total,
                "correct": correct,
                "accuracy": accuracy,
                "by_category": {k: v for k, v in by_category.items()},
                "by_failure": dict(by_failure),
                "questions": results,
            },
            f,
            indent=2,
        )

    print(f"\nResults saved to: {results_file}")


if __name__ == "__main__":
    main()
