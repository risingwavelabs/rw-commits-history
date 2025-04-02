#!/bin/bash
set -e

# Help function
show_help() {
    echo "Usage: $0 [--debug] [--output FILENAME]"
    echo "Generate RisingWave release timeline visualization"
    echo ""
    echo "Options:"
    echo "  --debug            Enable debug output"
    echo "  --output FILENAME  Specify output filename (default: release_timeline.png)"
    echo "  --help             Show this help message"
    echo ""
    echo "Environment Variables:"
    echo "  GITHUB_TOKEN       GitHub API token (required)"
    echo "  DEBUG              Set to 1 to enable debug output"
}

# Enable debug mode if --debug flag is provided
if [[ "$1" == "--debug" ]]; then
    echo "Debug mode enabled"
    export DEBUG=1
fi

# Check if .env file exists
if [ ! -f .env ]; then
    echo "Error: .env file not found."
    echo "Please create one based on .env.template"
    exit 1
fi

# Set output file name
OUTPUT_FILE="release_timeline.png"

# Run the Python script
uv run --with-requirements requirements.txt release_viz.py 

# Show output location
echo ""
echo "Timeline generated successfully!"
echo "- Image: $OUTPUT_FILE"
echo "- Markdown: ${OUTPUT_FILE%.png}.md" 