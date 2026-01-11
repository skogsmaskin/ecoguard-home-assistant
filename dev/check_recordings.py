#!/usr/bin/env python3
"""Debugging script to check the integrity of EcoGuard sensor recordings.

This script verifies that sensors are recording according to their specified
RECORDING_INTERVAL settings and reports any issues.

Usage:
    cd dev
    python3 check_recordings.py

The script will:
- Analyze all EcoGuard sensors in the Home Assistant database
- Check if sensors are recording according to their expected intervals
- Detect partial recordings in combining sensors (sensors that combine data from multiple sources)
- Report issues (sensors not recording when they should, partial recordings)
- Report warnings (sensors recording when they shouldn't, or only unknown states)
- Show statistics about recording intervals for sensors with timestamps

Note: Home Assistant automatically records the initial state when entities are first registered
(including on each restart). If a sensor doesn't have data yet, this initial state will be
"unknown". This is expected Home Assistant behavior and cannot be prevented. The integration's
code prevents writing "unknown" states programmatically, but the initial state recording on
entity registration is a core Home Assistant feature.

Expected intervals:
- Daily sensors: 86400 seconds (once per day, when date changes)
- Daily aggregate sensors: 3600 seconds (once per hour)
- Monthly sensors: 86400 seconds (daily for progression tracking)
- Reception sensors: Should NOT record (RECORDING_ENABLED = False)
"""

import sqlite3
import sys
from pathlib import Path
from typing import Any

# Expected recording intervals for different sensor types (in seconds)
EXPECTED_INTERVALS = {
    # Daily sensors - should record once per day (when date changes)
    "consumption_daily_metered": 86400,  # 24 hours
    "cost_daily_metered": 86400,
    "cost_daily_estimated": 86400,
    "consumption_daily_metered_": 86400,  # Individual meters
    "cost_daily_metered_": 86400,
    "cost_daily_estimated_": 86400,
    # Daily aggregate sensors - should record hourly
    "consumption_daily_metered_hot_water": 3600,  # 1 hour
    "consumption_daily_metered_cold_water": 3600,
    "consumption_daily_metered_combined_water": 3600,
    "cost_daily_metered_hot_water": 3600,
    "cost_daily_estimated_hot_water": 3600,
    "cost_daily_metered_cold_water": 3600,
    "cost_daily_estimated_cold_water": 3600,
    "cost_daily_metered_combined_water": 3600,
    "cost_daily_estimated_combined_water": 3600,
    # Monthly sensors - should record daily (for progression)
    "consumption_monthly_accumulated": 86400,  # Daily for progression
    "consumption_monthly_accumulated_": 86400,
    "cost_monthly_": 86400,
    # Special sensors
    "cost_monthly_total": 86400,
    "cost_monthly_other_items": 86400,
    "cost_monthly_estimated_final_settlement": 86400,
    # Reception sensors - should NOT record (RECORDING_ENABLED = False)
    "reception_last_update": None,  # Should not record
}

# Sensors that should not record
NO_RECORDING_SENSORS = [
    "reception_last_update",
]


def get_expected_interval(entity_id: str) -> int | None:
    """Get expected recording interval for an entity_id."""
    for pattern, interval in EXPECTED_INTERVALS.items():
        if pattern in entity_id:
            return interval
    # Default: daily sensors record daily, others record all updates
    if "daily" in entity_id:
        return 86400
    return None  # Record all updates


def should_record(entity_id: str) -> bool:
    """Check if an entity should be recorded."""
    for pattern in NO_RECORDING_SENSORS:
        if pattern in entity_id:
            return False
    return True


def analyze_recordings(db_path: Path) -> dict[str, Any]:
    """Analyze recordings from the database."""
    if not db_path.exists():
        return {"error": f"Database not found: {db_path}"}

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    try:
        # Get all ecoguard sensor states
        query = """
            SELECT 
                sm.entity_id,
                sm.metadata_id,
                s.state,
                s.last_changed_ts,
                s.last_updated_ts,
                COUNT(*) as state_count
            FROM states s
            JOIN states_meta sm ON s.metadata_id = sm.metadata_id
            WHERE sm.entity_id LIKE 'sensor.%ecoguard%'
               OR sm.entity_id LIKE 'sensor.consumption_%'
               OR sm.entity_id LIKE 'sensor.cost_%'
               OR sm.entity_id LIKE 'sensor.reception_%'
            GROUP BY sm.entity_id, sm.metadata_id
            ORDER BY sm.entity_id
        """
        cursor = conn.execute(query)
        sensors = cursor.fetchall()

        # Get detailed state history for each sensor
        sensor_data = {}
        for sensor in sensors:
            entity_id = sensor["entity_id"]
            metadata_id = sensor["metadata_id"]

            # Get all states for this sensor, ordered by time
            # Include states even if timestamp is 0 (initial states)
            history_query = """
                SELECT 
                    state,
                    last_changed_ts,
                    last_updated_ts
                FROM states
                WHERE metadata_id = ?
                ORDER BY COALESCE(last_changed_ts, 0) ASC, state_id ASC
            """
            history_cursor = conn.execute(history_query, (metadata_id,))
            history = history_cursor.fetchall()

            # Filter to only states with valid timestamps for interval analysis
            history_with_timestamps = [
                h
                for h in history
                if h["last_changed_ts"] is not None and h["last_changed_ts"] > 0
            ]

            sensor_data[entity_id] = {
                "metadata_id": metadata_id,
                "state_count": sensor["state_count"],
                "current_state": sensor["state"],
                "history": history,
                "history_with_timestamps": history_with_timestamps,
                "expected_interval": get_expected_interval(entity_id),
                "should_record": should_record(entity_id),
            }

        if not sensor_data:
            return {"sensors": {}, "error": "No EcoGuard sensors found in database"}
        return {"sensors": sensor_data, "error": None}

    finally:
        conn.close()


def calculate_intervals(history: list) -> list[float]:
    """Calculate time intervals between consecutive state changes."""
    intervals = []
    for i in range(1, len(history)):
        prev_ts = history[i - 1]["last_changed_ts"]
        curr_ts = history[i]["last_changed_ts"]
        if prev_ts and curr_ts:
            interval = curr_ts - prev_ts
            intervals.append(interval)
    return intervals


def format_duration(seconds: float) -> str:
    """Format duration in human-readable format."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        return f"{seconds/60:.1f}m"
    elif seconds < 86400:
        return f"{seconds/3600:.1f}h"
    else:
        return f"{seconds/86400:.1f}d"


def is_combining_sensor(entity_id: str) -> bool:
    """Check if a sensor combines data from multiple sources."""
    combining_patterns = [
        "combined_water",
        "total_monthly",
        "total_estimated",
        "total_metered",
    ]
    return any(pattern in entity_id for pattern in combining_patterns)


def detect_partial_recordings(entity_id: str, history: list) -> list[str]:
    """Detect if sensor recorded partial values before all dependencies were available.

    Returns list of issues found.
    """
    issues = []

    if not is_combining_sensor(entity_id):
        return issues

    # Extract numeric values with their order (excluding None and "unknown")
    numeric_values_with_order = []
    for idx, h in enumerate(history):
        state = h["state"]
        if state is not None and state != "unknown":
            try:
                value = float(state)
                numeric_values_with_order.append((value, idx))
            except (ValueError, TypeError):
                # Intentionally ignore non-numeric or malformed states; they are not
                # relevant for numeric analysis of partial recordings.
                continue

    # Get unique values in order of appearance
    seen_values = {}
    for value, idx in numeric_values_with_order:
        if value not in seen_values:
            seen_values[value] = idx

    unique_values_ordered = sorted(seen_values.items(), key=lambda x: x[1])
    unique_values = [v[0] for v in unique_values_ordered]

    # Check if we have multiple different values (potential partial recordings)
    if len(unique_values) > 1:
        # Check if values are increasing (suggests partial -> complete)
        # For combining sensors, values should only increase as more dependencies become available
        for i in range(len(unique_values) - 1):
            smaller = unique_values[i]
            larger = unique_values[i + 1]

            # Check if smaller value appears to be a partial total
            # (e.g., 195.0 followed by 369.0 suggests 195 was just CW, 369 is HW+CW)
            if larger > smaller:
                # Check if the difference suggests a missing component
                # For water sensors, HW is typically 40-60% of total, so if difference
                # is 30-70% of larger value, it might be a missing component
                difference = larger - smaller
                if difference > 0:
                    ratio = difference / larger if larger > 0 else 0
                    # If the difference is 20-80% of the total, it might be a missing component
                    # Also check if smaller is a significant portion (not just rounding differences)
                    if 0.2 <= ratio <= 0.8 and smaller > 0:
                        # Calculate what percentage the smaller value is of the larger
                        smaller_ratio = smaller / larger if larger > 0 else 0
                        issues.append(
                            f"⚠️  PARTIAL RECORDING: Recorded {smaller:.1f} before complete data "
                            f"(final: {larger:.1f}, difference: {difference:.1f}, {ratio*100:.0f}% of total). "
                            f"This suggests the sensor recorded when only {smaller_ratio*100:.0f}% of dependencies "
                            f"were available. Sensor should wait for all dependencies before recording."
                        )
                        break  # Only report once per sensor

        # Also check if values decreased (unusual for totals, might indicate data correction)
        if len(unique_values) >= 2:
            if unique_values[-1] < unique_values[0]:
                issues.append(
                    f"Unusual: value decreased from {unique_values[0]:.1f} to {unique_values[-1]:.1f}. "
                    f"This might indicate data correction or calculation changes."
                )

    return issues


def check_sensor_integrity(entity_id: str, sensor_info: dict) -> dict[str, Any]:
    """Check integrity of a single sensor's recordings."""
    issues = []
    warnings = []
    stats = {}

    history = sensor_info["history"]
    history_with_timestamps = sensor_info.get("history_with_timestamps", [])
    expected_interval = sensor_info["expected_interval"]
    should_record = sensor_info["should_record"]

    # Check for partial recordings in combining sensors (must be done before other checks)
    partial_issues = detect_partial_recordings(entity_id, history)
    issues.extend(partial_issues)

    # Check if sensor should record but isn't
    if should_record and len(history) == 0:
        issues.append("Sensor should record but has no recorded states")
    elif not should_record and len(history) > 0:
        warnings.append(
            f"Sensor should NOT record but has {len(history)} recorded states"
        )

    # Check recording intervals (only use states with valid timestamps)
    if len(history_with_timestamps) > 1 and expected_interval:
        intervals = calculate_intervals(history_with_timestamps)
        if intervals:
            avg_interval = sum(intervals) / len(intervals)
            min_interval = min(intervals)
            max_interval = max(intervals)

            stats["avg_interval"] = avg_interval
            stats["min_interval"] = min_interval
            stats["max_interval"] = max_interval

            # Check if intervals match expected
            # Allow some tolerance (within 20% of expected)
            tolerance = expected_interval * 0.2
            if avg_interval < expected_interval - tolerance:
                issues.append(
                    f"Average interval ({format_duration(avg_interval)}) is shorter than expected ({format_duration(expected_interval)})"
                )
            elif avg_interval > expected_interval + tolerance * 2:
                # More lenient for longer intervals (value-based writes)
                warnings.append(
                    f"Average interval ({format_duration(avg_interval)}) is longer than expected ({format_duration(expected_interval)}) - may be due to value-based writes"
                )

    # Check for unknown states
    unknown_count = sum(
        1 for h in history if h["state"] == "unknown" or h["state"] is None
    )
    if unknown_count > 0:
        stats["unknown_states"] = unknown_count
        if unknown_count == len(history):
            warnings.append(
                "All recorded states are 'unknown' - sensor may not have data yet"
            )
        elif unknown_count > 0 and unknown_count < len(history):
            # Some unknown states but also valid values - likely from HA initial state recording
            # This is expected behavior when HA restarts (HA records initial state on entity registration)
            # This is normal and cannot be prevented - it's a core Home Assistant feature
            warnings.append(
                f"{unknown_count} 'unknown' state(s) recorded (expected: HA records initial state on entity registration/restart)"
            )

    # Check state changes
    unique_states = set(
        h["state"] for h in history if h["state"] not in (None, "unknown")
    )
    stats["unique_states"] = len(unique_states)
    if unique_states:
        # Convert to numeric for sorting if possible
        try:
            numeric_states = sorted(
                [
                    float(s)
                    for s in unique_states
                    if isinstance(s, (int, float))
                    or (
                        isinstance(s, str)
                        and s.replace(".", "").replace("-", "").isdigit()
                    )
                ]
            )
            stats["state_values"] = [str(v) for v in numeric_states[:5]]
        except (ValueError, TypeError):
            stats["state_values"] = sorted(list(unique_states))[:5]
    if len(unique_states) == 0 and len(history) > 0:
        warnings.append("No valid state values recorded (all unknown/None)")

    # Additional check: if combining sensor has multiple values, show them
    if is_combining_sensor(entity_id) and len(unique_states) > 1:
        numeric_values = []
        for s in unique_states:
            try:
                numeric_values.append(float(s))
            except (ValueError, TypeError):
                pass
        if len(numeric_values) > 1:
            stats["combining_sensor_values"] = sorted(numeric_values)
            if len(numeric_values) >= 2 and numeric_values[-1] > numeric_values[0]:
                # Show the progression
                stats["value_progression"] = (
                    f"{numeric_values[0]} → {numeric_values[-1]}"
                )
        # Non-numeric state values are expected here and are intentionally
        # ignored; they are not relevant for combining sensor statistics.

    return {
        "issues": issues,
        "warnings": warnings,
        "stats": stats,
    }


def print_report(results: dict[str, Any]) -> None:
    """Print a formatted report."""
    if results.get("error"):
        print(f"❌ Error: {results['error']}")
        return

    sensors = results["sensors"]
    if not sensors:
        print("⚠️  No EcoGuard sensors found in database")
        return

    print("=" * 80)
    print("EcoGuard Recording Integrity Report")
    print("=" * 80)
    print(f"\nTotal sensors found: {len(sensors)}\n")

    # Categorize sensors
    recording_sensors = {k: v for k, v in sensors.items() if v["should_record"]}
    non_recording_sensors = {k: v for k, v in sensors.items() if not v["should_record"]}

    print(f"📊 Sensors that should record: {len(recording_sensors)}")
    print(f"🚫 Sensors that should NOT record: {len(non_recording_sensors)}\n")

    # Check each sensor
    total_issues = 0
    total_warnings = 0

    print("=" * 80)
    print("DETAILED ANALYSIS")
    print("=" * 80)

    for entity_id in sorted(sensors.keys()):
        sensor_info = sensors[entity_id]
        integrity = check_sensor_integrity(entity_id, sensor_info)

        issues = integrity["issues"]
        warnings = integrity["warnings"]
        stats = integrity["stats"]

        # Show sensor if it has issues, warnings, has recorded states, or has non-unknown values
        history_with_timestamps = sensor_info.get("history_with_timestamps", [])
        has_recorded_states = len(history_with_timestamps) > 0
        has_valid_values = (
            sensor_info["current_state"] not in (None, "unknown")
            or stats.get("unique_states", 0) > 0
        )

        # Mark if this is a combining sensor
        is_combining = is_combining_sensor(entity_id)

        if (
            issues
            or warnings
            or has_recorded_states
            or has_valid_values
            or sensor_info["state_count"] > 1
        ):
            print(f"\n📌 {entity_id}")
            if is_combining:
                print(f"   🔗 Combining sensor (combines data from multiple sources)")
            print(f"   States recorded: {sensor_info['state_count']}")
            print(f"   Current state: {sensor_info['current_state']}")
            print(f"   Should record: {sensor_info['should_record']}")
            if sensor_info["expected_interval"]:
                print(
                    f"   Expected interval: {format_duration(sensor_info['expected_interval'])}"
                )

            # Show state history summary
            if len(history_with_timestamps) > 0:
                print(f"   States with timestamps: {len(history_with_timestamps)}")
            elif len(sensor_info["history"]) > 0:
                print(
                    f"   Total states: {len(sensor_info['history'])} (no timestamps for interval analysis)"
                )

            if stats:
                if "avg_interval" in stats:
                    print(
                        f"   ✅ Average interval: {format_duration(stats['avg_interval'])}"
                    )
                    print(f"   Min interval: {format_duration(stats['min_interval'])}")
                    print(f"   Max interval: {format_duration(stats['max_interval'])}")
                if "unique_states" in stats:
                    print(f"   Unique state values: {stats['unique_states']}")
                    if "state_values" in stats:
                        print(
                            f"   Sample values: {', '.join(str(v) for v in stats['state_values'])}"
                        )
                    # Show value progression for combining sensors
                    if "value_progression" in stats:
                        print(
                            f"   ⚠️  Value progression: {stats['value_progression']} (may indicate partial recordings)"
                        )
                    if (
                        "combining_sensor_values" in stats
                        and len(stats["combining_sensor_values"]) > 1
                    ):
                        values = stats["combining_sensor_values"]
                        print(
                            f"   📊 All recorded values: {', '.join(f'{v:.1f}' for v in values)}"
                        )
                if "unknown_states" in stats:
                    print(f"   Unknown states: {stats['unknown_states']}")

            if issues:
                total_issues += len(issues)
                for issue in issues:
                    print(f"   ❌ ISSUE: {issue}")
            if warnings:
                total_warnings += len(warnings)
                for warning in warnings:
                    print(f"   ⚠️  WARNING: {warning}")

    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Total sensors analyzed: {len(sensors)}")
    print(f"Total issues found: {total_issues}")
    print(f"Total warnings: {total_warnings}")

    # Count combining sensors and their issues
    combining_sensors = {k: v for k, v in sensors.items() if is_combining_sensor(k)}
    combining_issues = sum(
        1
        for k in combining_sensors.keys()
        if check_sensor_integrity(k, sensors[k])["issues"]
    )
    if combining_sensors:
        print(f"\n🔗 Combining sensors: {len(combining_sensors)}")
        if combining_issues > 0:
            print(
                f"   ⚠️  {combining_issues} combining sensor(s) with issues (may have recorded partial data)"
            )

    if total_issues == 0 and total_warnings == 0:
        print("\n✅ All sensors appear to be recording correctly!")
    elif total_issues == 0:
        print("\n⚠️  Some warnings found, but no critical issues")
    else:
        print("\n❌ Some issues found - review the detailed analysis above")
        if combining_issues > 0:
            print(
                "\n💡 Tip: Combining sensors should wait for all dependencies before recording."
            )
            print(
                "   If you see 'Possible partial recording detected', the sensor recorded"
            )
            print("   a partial value before all data sources were available.")


def main():
    """Main entry point."""
    # Find database
    script_dir = Path(__file__).parent
    db_path = script_dir / "home-assistant_v2.db"

    if not db_path.exists():
        # Try alternative location
        db_path = script_dir / ".homeassistant" / "home-assistant_v2.db"
        if not db_path.exists():
            print(f"❌ Database not found. Tried:")
            print(f"   - {script_dir / 'home-assistant_v2.db'}")
            print(f"   - {script_dir / '.homeassistant' / 'home-assistant_v2.db'}")
            sys.exit(1)

    print(f"📁 Using database: {db_path}")
    print()

    # Analyze recordings
    results = analyze_recordings(db_path)

    # Print report
    print_report(results)


if __name__ == "__main__":
    main()
