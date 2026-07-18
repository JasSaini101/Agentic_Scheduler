from dotenv import load_dotenv
load_dotenv()

from scheduler.llm.pipeline import pipeline
from scheduler.llm.adjustment_handler import apply as apply_adjustment


def main():
    print("=== Agentic Scheduler ===\n")
    goals = input("What are your goals for this week? ").strip()
    if not goals:
        print("No goals provided. Exiting.")
        return

    print("\n[main] Parsing goals and building schedule...")
    schedule, user_config, calendar_error, _constraints = pipeline.invoke(goals)
    if calendar_error:
        print(f"\n⚠️  {calendar_error}")

    print("\n")
    change = input("Any changes? (press Enter to skip) ").strip()
    if change:
        schedule = apply_adjustment(change, schedule, user_config)
        print("\n[main] Updated schedule after adjustments:")
        for e in sorted(schedule, key=lambda e: (e.day, e.start_hour)):
            print(f"  {e.day} {e.start_hour}:00 — {e.label}")


if __name__ == "__main__":
    main()
