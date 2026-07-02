from dotenv import load_dotenv
load_dotenv()

from scheduler.llm.goal_parser import parse_goals
from scheduler.llm.adjustment_handler import apply as apply_adjustment
from scheduler import orchestrator


def main():
    print("=== Agentic Scheduler ===\n")
    goals = input("What are your goals for this week? ").strip()
    if not goals:
        print("No goals provided. Exiting.")
        return

    print("\n[main] Parsing goals...")
    user_config = parse_goals(goals)
    print(f"[main] Parsed {len(user_config.activities)} activities:")
    for a in user_config.activities:
        print(f"  - {a.name} ({a.category}): {a.sessions_per_week}x/week × {a.duration_hours}h")

    schedule = orchestrator.run(user_config)

    print("\n")
    change = input("Any changes? (press Enter to skip) ").strip()
    if change:
        schedule = apply_adjustment(change, schedule, user_config)
        print("\n[main] Updated schedule after adjustments:")
        for e in sorted(schedule, key=lambda e: (e.day, e.start_hour)):
            print(f"  {e.day} {e.start_hour}:00 — {e.label}")


if __name__ == "__main__":
    main()
