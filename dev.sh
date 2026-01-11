#!/bin/bash
# Development convenience script for EcoGuard Home Assistant integration.
# Provides various development commands for testing, debugging, and maintenance.

set -uo pipefail

# Get the repository root directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
DEV_DIR="$REPO_ROOT/dev"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Print error message
error() {
    echo -e "${RED}❌ $1${NC}" >&2
}

# Print success message
success() {
    echo -e "${GREEN}✅ $1${NC}"
}

# Print info message
info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

# Print warning message
warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

# Check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Check recordings
cmd_check_recordings() {
    local script="$DEV_DIR/check_recordings.py"

    if [[ ! -f "$script" ]]; then
        error "Recording check script not found: $script"
        return 1
    fi

    info "Checking recording integrity..."
    python3 "$script"
}

# Run Home Assistant
cmd_run_hass() {
    if [[ ! -d "$DEV_DIR" ]]; then
        error "Dev directory not found: $DEV_DIR"
        return 1
    fi

    # Ensure symlink exists
    local symlink="$DEV_DIR/custom_components/ecoguard"
    if [[ ! -e "$symlink" ]]; then
        info "Creating symlink to custom_components..."
        mkdir -p "$(dirname "$symlink")"
        ln -sf "$REPO_ROOT/custom_components/ecoguard" "$symlink"
    fi

    # Check for virtual environment
    local venv="$REPO_ROOT/venv"
    if [[ -d "$venv" ]]; then
        info "Activating virtual environment..."
        source "$venv/bin/activate"
    fi

    # Check if hass is available
    if ! command_exists hass; then
        error "Home Assistant (hass) not found."
        echo "   Install with: pip install homeassistant"
        return 1
    fi

    info "Starting Home Assistant..."
    echo "   Config directory: $DEV_DIR"
    echo ""

    cd "$DEV_DIR"
    hass --config .
}

# View logs
cmd_logs() {
    local log_file="$DEV_DIR/.homeassistant/home-assistant.log"
    local lines="${1:-50}"
    local follow="${2:-false}"

    if [[ ! -f "$log_file" ]]; then
        # Try alternative location
        log_file="$DEV_DIR/home-assistant.log"
        if [[ ! -f "$log_file" ]]; then
            error "Log file not found. Tried:"
            echo "   - $DEV_DIR/.homeassistant/home-assistant.log"
            echo "   - $DEV_DIR/home-assistant.log"
            return 1
        fi
    fi

    if [[ "$follow" == "true" ]]; then
        info "Following logs (last $lines lines, Ctrl+C to exit)..."
        tail -f -n "$lines" "$log_file"
    else
        info "Showing last $lines lines of logs..."
        tail -n "$lines" "$log_file"
    fi
}

# Database operations
cmd_db() {
    local db_file="$DEV_DIR/home-assistant_v2.db"
    local operation="$1"

    if [[ ! -f "$db_file" ]]; then
        # Try alternative location
        db_file="$DEV_DIR/.homeassistant/home-assistant_v2.db"
        if [[ ! -f "$db_file" ]]; then
            error "Database not found. Tried:"
            echo "   - $DEV_DIR/home-assistant_v2.db"
            echo "   - $DEV_DIR/.homeassistant/home-assistant_v2.db"
            return 1
        fi
    fi

    if [[ "$operation" == "info" ]]; then
        info "Database information:"
        echo ""

        # Check if sqlite3 is available
        if ! command_exists sqlite3; then
            error "sqlite3 not found. Install with: sudo apt-get install sqlite3"
            return 1
        fi

        # Get table count
        local table_count
        table_count=$(sqlite3 "$db_file" "SELECT COUNT(*) FROM sqlite_master WHERE type='table'" 2>/dev/null || echo "0")

        # Get state count
        local state_count
        state_count=$(sqlite3 "$db_file" "SELECT COUNT(*) FROM states" 2>/dev/null || echo "0")

        # Get ecoguard state count
        local ecoguard_count
        ecoguard_count=$(sqlite3 "$db_file" "
            SELECT COUNT(*) FROM states s
            JOIN states_meta sm ON s.metadata_id = sm.metadata_id
            WHERE sm.entity_id LIKE '%ecoguard%'
               OR sm.entity_id LIKE '%consumption%'
               OR sm.entity_id LIKE '%cost%'
        " 2>/dev/null || echo "0")

        # Get database size
        local db_size
        db_size=$(stat -f%z "$db_file" 2>/dev/null || stat -c%s "$db_file" 2>/dev/null || echo "0")
        local size_str
        if [[ $db_size -lt 1024 ]]; then
            size_str="${db_size} B"
        elif [[ $db_size -lt $((1024 * 1024)) ]]; then
            size_str=$(awk "BEGIN {printf \"%.1f KB\", $db_size/1024}")
        else
            size_str=$(awk "BEGIN {printf \"%.1f MB\", $db_size/(1024*1024)}")
        fi

        echo "📊 Database: $db_file"
        echo "📋 Tables: $table_count"
        echo ""
        printf "   States: %'d\n" "$state_count"
        printf "   EcoGuard states: %'d\n" "$ecoguard_count"
        echo "   Size: $size_str"

    elif [[ "$operation" == "query" ]]; then
        local query="$2"

        if [[ -z "$query" ]]; then
            error "Query not provided"
            return 1
        fi

        if ! command_exists sqlite3; then
            error "sqlite3 not found. Install with: sudo apt-get install sqlite3"
            return 1
        fi

        sqlite3 "$db_file" "$query"

    else
        info "Database operations"
        echo ""
        echo "Available options:"
        echo "  info          Show database information"
        echo "  query SQL     Execute SQL query"
        echo ""
        echo "Examples:"
        echo "  $0 db info"
        echo '  $0 db query "SELECT COUNT(*) FROM states"'
    fi
}

# Run tests
cmd_test() {
    local verbose="${1:-false}"
    local coverage="${2:-false}"
    local test_file="${3:-}"
    local test_pattern="${4:-}"

    # Check for virtual environment
    local venv="$REPO_ROOT/venv"
    local python_cmd="python3"

    if [[ -d "$venv" ]]; then
        info "Activating virtual environment..."
        source "$venv/bin/activate"
        python_cmd="python3"
    fi

    # Check if pytest is available
    if ! "$python_cmd" -m pytest --version >/dev/null 2>&1; then
        error "pytest not found"
        echo ""
        echo "Install test dependencies with:"
        echo "  pip install -r tests/requirements.txt"
        echo ""
        echo "Or if using a virtual environment:"
        echo "  source venv/bin/activate"
        echo "  pip install -r tests/requirements.txt"
        return 1
    fi

    info "Running tests..."

    local test_cmd=("$python_cmd" "-m" "pytest")

    if [[ "$verbose" == "true" ]]; then
        test_cmd+=("-v")
    fi

    if [[ "$coverage" == "true" ]]; then
        test_cmd+=("--cov=custom_components/ecoguard" "--cov-report=term-missing")
    fi

    if [[ -n "$test_file" ]]; then
        test_cmd+=("$test_file")
    elif [[ -n "$test_pattern" ]]; then
        test_cmd+=("-k" "$test_pattern")
    fi

    cd "$REPO_ROOT"
    "${test_cmd[@]}"
}

# Nuke (delete) all EcoGuard recordings
cmd_nuke_recordings() {
    local db_file="$DEV_DIR/home-assistant_v2.db"
    local confirm="${1:-false}"

    if [[ ! -f "$db_file" ]]; then
        # Try alternative location
        db_file="$DEV_DIR/.homeassistant/home-assistant_v2.db"
        if [[ ! -f "$db_file" ]]; then
            error "Database not found. Tried:"
            echo "   - $DEV_DIR/home-assistant_v2.db"
            echo "   - $DEV_DIR/.homeassistant/home-assistant_v2.db"
            return 1
        fi
    fi

    if ! command_exists sqlite3; then
        error "sqlite3 not found. Install with: sudo apt-get install sqlite3"
        return 1
    fi

    # Count existing recordings
    local count
    count=$(sqlite3 "$db_file" "
        SELECT COUNT(*) FROM states s
        JOIN states_meta sm ON s.metadata_id = sm.metadata_id
        WHERE sm.entity_id LIKE '%ecoguard%'
           OR sm.entity_id LIKE '%consumption%'
           OR sm.entity_id LIKE '%cost%'
    " 2>/dev/null || echo "0")

    if [[ "$count" -eq 0 ]]; then
        info "No EcoGuard recordings found in database"
        return 0
    fi

    if [[ "$confirm" != "yes" ]]; then
        warning "This will delete $count EcoGuard state recordings from the database!"
        echo ""
        echo "This action cannot be undone."
        echo ""
        echo "To confirm, run:"
        echo "  $0 nuke-recordings --confirm"
        return 1
    fi

    info "Deleting $count EcoGuard state recordings..."

    # Delete states for ecoguard sensors
    local deleted
    deleted=$(sqlite3 "$db_file" "
        DELETE FROM states
        WHERE metadata_id IN (
            SELECT metadata_id FROM states_meta
            WHERE entity_id LIKE '%ecoguard%'
               OR entity_id LIKE '%consumption%'
               OR entity_id LIKE '%cost%'
        );
        SELECT changes();
    " 2>/dev/null || echo "0")

    if [[ "$deleted" -gt 0 ]]; then
        success "Deleted $deleted EcoGuard state recordings"

        # Optionally clean up statistics (user might want this too)
        info "Note: Statistics are not automatically deleted."
        info "To clean statistics, you may need to restart Home Assistant or use the recorder service."
    else
        warning "No recordings were deleted (database may be locked or already empty)"
    fi
}

# Install test dependencies
cmd_install_test_deps() {
    info "Installing test dependencies..."

    # Check for virtual environment
    local venv="$REPO_ROOT/venv"
    local pip_cmd="pip3"

    if [[ -d "$venv" ]]; then
        info "Activating virtual environment..."
        source "$venv/bin/activate"
        pip_cmd="pip"
    fi

    local requirements_file="$REPO_ROOT/tests/requirements.txt"

    if [[ ! -f "$requirements_file" ]]; then
        error "Test requirements file not found: $requirements_file"
        return 1
    fi

    info "Installing from $requirements_file..."
    "$pip_cmd" install -r "$requirements_file"
}

# Run linters
cmd_lint() {
    info "Running linters..."

    local exit_code=0

    # Run ruff if available
    if command_exists ruff; then
        info "Running ruff..."
        if ! ruff check custom_components/ecoguard; then
            exit_code=1
        fi
    else
        warning "ruff not found, skipping..."
    fi

    # Run mypy if available
    if command_exists mypy; then
        info "Running mypy..."
        if ! mypy custom_components/ecoguard; then
            exit_code=1
        fi
    else
        warning "mypy not found, skipping..."
    fi

    return $exit_code
}

# Show usage
show_usage() {
    cat << EOF
Usage: $0 <command> [options]

EcoGuard Home Assistant - Development Tools

Commands:
  check-recordings    Check recording integrity
  nuke-recordings     Delete all EcoGuard recordings (use --confirm)
  run-hass, hass      Run Home Assistant in dev mode
  logs [options]      View Home Assistant logs
  db [operation]      Database operations
  test [options]      Run tests
  install-test-deps   Install test dependencies
  lint                Run linters

Options:
  logs:
    -n, --lines N    Number of lines to show (default: 50)
    -f, --follow      Follow logs in real-time

  db:
    info              Show database information
    query SQL         Execute SQL query

  test:
    -v, --verbose     Verbose output
    --coverage        Show coverage report
    --file FILE       Run specific test file
    --test PATTERN    Run specific test (pattern)

Examples:
  $0 check-recordings
  $0 nuke-recordings --confirm    # Delete all EcoGuard recordings
  $0 run-hass
  $0 logs --follow
  $0 db info
  $0 install-test-deps    # Install test dependencies first
  $0 test --coverage
  $0 lint

EOF
}

# Main
main() {
    if [[ $# -eq 0 ]]; then
        show_usage
        exit 1
    fi

    local command="$1"
    shift || true

    case "$command" in
        check-recordings)
            cmd_check_recordings
            ;;
        run-hass|hass)
            cmd_run_hass
            ;;
        logs)
            local lines=50
            local follow=false

            while [[ $# -gt 0 ]]; do
                case "$1" in
                    -n|--lines)
                        lines="$2"
                        shift 2
                        ;;
                    -f|--follow)
                        follow=true
                        shift
                        ;;
                    *)
                        error "Unknown option: $1"
                        show_usage
                        exit 1
                        ;;
                esac
            done

            cmd_logs "$lines" "$follow"
            ;;
        db)
            if [[ $# -eq 0 ]]; then
                cmd_db ""
            else
                local operation="$1"
                shift || true

                if [[ "$operation" == "info" ]]; then
                    cmd_db "info"
                elif [[ "$operation" == "query" ]]; then
                    if [[ $# -eq 0 ]]; then
                        error "Query not provided"
                        show_usage
                        exit 1
                    fi
                    cmd_db "query" "$*"
                else
                    error "Unknown database operation: $operation"
                    cmd_db ""
                    exit 1
                fi
            fi
            ;;
        test)
            local verbose=false
            local coverage=false
            local test_file=""
            local test_pattern=""

            while [[ $# -gt 0 ]]; do
                case "$1" in
                    -v|--verbose)
                        verbose=true
                        shift
                        ;;
                    --coverage)
                        coverage=true
                        shift
                        ;;
                    --file)
                        test_file="$2"
                        shift 2
                        ;;
                    --test)
                        test_pattern="$2"
                        shift 2
                        ;;
                    *)
                        error "Unknown option: $1"
                        show_usage
                        exit 1
                        ;;
                esac
            done

            cmd_test "$verbose" "$coverage" "$test_file" "$test_pattern"
            ;;
        nuke-recordings)
            local confirm="no"
            if [[ "${1:-}" == "--confirm" ]]; then
                confirm="yes"
            fi
            cmd_nuke_recordings "$confirm"
            ;;
        install-test-deps)
            cmd_install_test_deps
            ;;
        lint)
            cmd_lint
            ;;
        -h|--help|help)
            show_usage
            ;;
        *)
            error "Unknown command: $command"
            show_usage
            exit 1
            ;;
    esac
}

# Run main function
main "$@"
