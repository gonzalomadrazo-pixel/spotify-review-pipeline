"""Cost and runtime calculator for the pipeline.

This module calculates API costs and runtime for a pilot run on 100 reviews,
with projected estimates for the full corpus. It reads saved API call logs
and usage data, and can replay without model calls (offline mode).
"""
import json
import csv
from pathlib import Path
from datetime import datetime
from typing import Optional
import subprocess


class CostCalculator:
    """Calculate cost and runtime from pilot and projections."""

    def __init__(self, pilot_dir: Path = Path("cost")):
        self.pilot_dir = Path(pilot_dir)
        self.pilot_dir.mkdir(exist_ok=True)

        self.rates = self._load_or_default_rates()
        self.usage = {}
        self.timings = {}

    def _load_or_default_rates(self) -> dict:
        """Load editable rates.csv or use defaults."""
        rates_path = self.pilot_dir / "rates.csv"

        if rates_path.exists():
            rates = {}
            with rates_path.open() as f:
                for row in csv.DictReader(f):
                    key = f"{row['model']}_{row['billing_unit']}"
                    rates[key] = float(row['price_per_unit'])
            return rates

        # Default Anthropic API rates (Oct 2026)
        # Input / output per million tokens
        return {
            "claude-haiku-4-5-20251001_input": 1.0 / 1_000_000,      # $1 / 1M input
            "claude-haiku-4-5-20251001_output": 5.0 / 1_000_000,     # $5 / 1M output
            "claude-opus-5-5_input": 4.0 / 1_000_000,                # $4 / 1M input
            "claude-opus-5-5_output": 20.0 / 1_000_000,              # $20 / 1M output
        }

    def record_call(self, model: str, role: str, input_tokens: int,
                    output_tokens: int, wall_time_sec: float):
        """Record an API call for cost tracking."""
        key = f"{model}_{role}"
        if key not in self.usage:
            self.usage[key] = {"input": 0, "output": 0, "calls": 0}

        self.usage[key]["input"] += input_tokens
        self.usage[key]["output"] += output_tokens
        self.usage[key]["calls"] += 1

        if key not in self.timings:
            self.timings[key] = {"total_sec": 0, "calls": 0}

        self.timings[key]["total_sec"] += wall_time_sec
        self.timings[key]["calls"] += 1

    def calculate_cost(self) -> dict:
        """Calculate total API cost from recorded calls."""
        total_cost = 0.0
        breakdown = {}

        for key, tokens in self.usage.items():
            model = key.split("_")[0]

            input_cost = tokens["input"] * self.rates.get(f"{model}_input", 0)
            output_cost = tokens["output"] * self.rates.get(f"{model}_output", 0)
            subtotal = input_cost + output_cost

            breakdown[key] = {
                "calls": tokens["calls"],
                "input_tokens": tokens["input"],
                "output_tokens": tokens["output"],
                "input_cost": round(input_cost, 6),
                "output_cost": round(output_cost, 6),
                "subtotal": round(subtotal, 6),
            }

            total_cost += subtotal

        return {
            "total_usd": round(total_cost, 6),
            "breakdown": breakdown,
            "timestamp": datetime.now().isoformat(),
        }

    def calculate_throughput(self) -> dict:
        """Calculate throughput (reviews/sec) by stage."""
        throughput = {}

        for key, times in self.timings.items():
            if times["calls"] > 0:
                avg_sec = times["total_sec"] / times["calls"]
                reviews_per_sec = 1.0 / avg_sec if avg_sec > 0 else 0
                throughput[key] = {
                    "avg_sec_per_call": round(avg_sec, 3),
                    "reviews_per_sec": round(reviews_per_sec, 3),
                    "calls": times["calls"],
                    "total_sec": round(times["total_sec"], 1),
                }

        return throughput

    def project_full_run(self, nonempty_reviews: int = 660_609,
                         distinct_texts: int = 484_189) -> dict:
        """Project cost and time for the full run."""
        # Assumptions:
        # - Full run uses same model and throughput as pilot
        # - Exact-text caching reduces distinct work from nonempty to distinct_texts
        # - Each stage has its own throughput

        if not self.usage or not self.timings:
            return {
                "status": "no_pilot_data",
                "message": "Run pilot first to establish throughput and token counts."
            }

        # Simple projection: avg tokens per review * distinct texts
        avg_input_tokens = 0
        avg_output_tokens = 0
        call_count = 0

        for key, tokens in self.usage.items():
            if tokens["calls"] > 0:
                avg_input_tokens += tokens["input"] / tokens["calls"]
                avg_output_tokens += tokens["output"] / tokens["calls"]
                call_count += 1

        if call_count > 0:
            avg_input_tokens /= call_count
            avg_output_tokens /= call_count

        projected_calls = distinct_texts  # One call per distinct text
        projected_input = int(avg_input_tokens * projected_calls)
        projected_output = int(avg_output_tokens * projected_calls)

        projected_cost = (
            projected_input * self.rates.get("claude-haiku-4-5-20251001_input", 0) +
            projected_output * self.rates.get("claude-haiku-4-5-20251001_output", 0)
        )

        # Throughput estimate
        avg_throughput = sum(
            t.get("reviews_per_sec", 1) for t in self.timings.values()
        ) / len(self.timings) if self.timings else 1

        projected_time_sec = (distinct_texts / avg_throughput) if avg_throughput > 0 else 0

        return {
            "nonempty_reviews": nonempty_reviews,
            "distinct_texts": distinct_texts,
            "projected_calls": projected_calls,
            "projected_input_tokens": projected_input,
            "projected_output_tokens": projected_output,
            "projected_cost_usd": round(projected_cost, 2),
            "projected_time_sec": round(projected_time_sec, 1),
            "projected_time_hours": round(projected_time_sec / 3600, 2),
        }

    def save_report(self, pilot_cost: dict, pilot_throughput: dict,
                    projection: dict, output_path: Optional[Path] = None) -> str:
        """Write HTML/text report."""
        if output_path is None:
            output_path = self.pilot_dir / "report.md"

        report = f"""# Cost and Runtime Report

Generated: {datetime.now().isoformat()}

## Pilot Run (100 reviews)

**Total Cost:** ${pilot_cost['total_usd']:.2f}

### Breakdown
"""

        for key, detail in pilot_cost["breakdown"].items():
            report += f"\n- **{key}**: {detail['calls']} calls, "
            report += f"{detail['input_tokens']} input tokens, "
            report += f"{detail['output_tokens']} output tokens → ${detail['subtotal']:.4f}"

        report += "\n\n## Throughput\n"

        for key, tp in pilot_throughput.items():
            report += f"\n- **{key}**: {tp['reviews_per_sec']:.2f} reviews/sec "
            report += f"({tp['avg_sec_per_call']:.2f}s per call)"

        report += "\n\n## Projection (Full Run)\n"

        proj = projection
        report += f"\n- **Distinct texts to classify:** {proj.get('distinct_texts', '?'):,}\n"
        report += f"- **Projected API cost:** ${proj.get('projected_cost_usd', 0):.2f}\n"
        report += f"- **Projected time:** {proj.get('projected_time_hours', 0):.1f} hours\n"

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report)

        return str(output_path)


if __name__ == "__main__":
    calc = CostCalculator()

    # Dummy pilot data
    calc.record_call("claude-haiku-4-5-20251001", "enrich", 50, 30, 1.5)
    calc.record_call("claude-haiku-4-5-20251001", "enrich", 45, 28, 1.4)

    cost = calc.calculate_cost()
    throughput = calc.calculate_throughput()
    proj = calc.project_full_run()

    print("Cost:", json.dumps(cost, indent=2))
    print("\nThroughput:", json.dumps(throughput, indent=2))
    print("\nProjection:", json.dumps(proj, indent=2))
