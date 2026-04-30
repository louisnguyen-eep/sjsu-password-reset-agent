"""Generate a PNG of the LangGraph architecture for slides/documentation.

Run: python3.14 draw_graph.py
Output: graph.png in the project root
"""
from dotenv import load_dotenv
load_dotenv()

from graph import build_graph


def main():
    graph = build_graph()
    png_bytes = graph.get_graph().draw_mermaid_png()

    with open("graph.png", "wb") as f:
        f.write(png_bytes)

    print("✓ Wrote graph.png")
    print("  Drop this into your slides or README.")

    # Also print the raw Mermaid source — useful if you want to embed
    # the diagram in markdown or tweak the layout in mermaid.live
    print("\n--- Mermaid source ---")
    print(graph.get_graph().draw_mermaid())


if __name__ == "__main__":
    main()