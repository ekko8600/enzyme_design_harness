#!/usr/bin/env python3
"""
Literature Database Search Script
Searches multiple literature databases and aggregates results.
"""

import argparse
import json
from datetime import datetime
from typing import Dict, List


def format_search_results(results: List[Dict], output_format: str = 'json') -> str:
    """
    Format search results for output.

    Args:
        results: List of search results
        output_format: Format (json, markdown, or bibtex)

    Returns:
        Formatted string
    """
    if output_format == 'json':
        return json.dumps(results, indent=2)

    elif output_format == 'markdown':
        md = "# Literature Search Results\n\n"
        md += f"**Search Date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        md += f"**Total Results**: {len(results)}\n\n"

        for i, result in enumerate(results, 1):
            md += f"## {i}. {result.get('title', 'Untitled')}\n\n"
            md += f"**Authors**: {result.get('authors', 'Unknown')}\n\n"
            md += f"**Year**: {result.get('year', 'N/A')}\n\n"
            md += f"**Source**: {result.get('source', 'Unknown')}\n\n"

            if result.get('abstract'):
                md += f"**Abstract**: {result['abstract']}\n\n"

            if result.get('doi'):
                md += f"**DOI**: [{result['doi']}](https://doi.org/{result['doi']})\n\n"

            if result.get('url'):
                md += f"**URL**: {result['url']}\n\n"

            if result.get('citations'):
                md += f"**Citations**: {result['citations']}\n\n"

            md += "---\n\n"

        return md

    elif output_format == 'bibtex':
        bibtex = ""
        for i, result in enumerate(results, 1):
            entry_type = result.get('type', 'article')
            cite_key = f"{result.get('first_author', 'unknown')}{result.get('year', '0000')}"

            bibtex += f"@{entry_type}{{{cite_key},\n"
            bibtex += f"  title = {{{result.get('title', '')}}},\n"
            bibtex += f"  author = {{{result.get('authors', '')}}},\n"
            bibtex += f"  year = {{{result.get('year', '')}}},\n"

            if result.get('journal'):
                bibtex += f"  journal = {{{result['journal']}}},\n"

            if result.get('volume'):
                bibtex += f"  volume = {{{result['volume']}}},\n"

            if result.get('pages'):
                bibtex += f"  pages = {{{result['pages']}}},\n"

            if result.get('doi'):
                bibtex += f"  doi = {{{result['doi']}}},\n"

            bibtex += "}\n\n"

        return bibtex

    else:
        raise ValueError(f"Unknown format: {output_format}")


def deduplicate_results(results: List[Dict]) -> List[Dict]:
    """
    Remove duplicate results based on DOI or title.

    Args:
        results: List of search results

    Returns:
        Deduplicated list
    """
    seen_dois = set()
    seen_titles = set()
    unique_results = []

    for result in results:
        doi = result.get('doi', '').lower().strip()
        title = result.get('title', '').lower().strip()

        # Check DOI first (more reliable)
        if doi and doi in seen_dois:
            continue

        # Check title as fallback
        if not doi and title in seen_titles:
            continue

        # Add to results
        if doi:
            seen_dois.add(doi)
        if title:
            seen_titles.add(title)

        unique_results.append(result)

    return unique_results


def rank_results(results: List[Dict], criteria: str = 'citations') -> List[Dict]:
    """
    Rank results by specified criteria.

    Args:
        results: List of search results
        criteria: Ranking criteria (citations, year, relevance)

    Returns:
        Ranked list
    """
    if criteria == 'citations':
        return sorted(results, key=lambda x: x.get('citations', 0), reverse=True)
    elif criteria == 'year':
        return sorted(results, key=lambda x: x.get('year', '0'), reverse=True)
    elif criteria == 'relevance':
        return sorted(results, key=lambda x: x.get('relevance_score', 0), reverse=True)
    else:
        return results


def filter_by_year(results: List[Dict], start_year: int = None, end_year: int = None) -> List[Dict]:
    """
    Filter results by publication year range.

    Args:
        results: List of search results
        start_year: Minimum year (inclusive)
        end_year: Maximum year (inclusive)

    Returns:
        Filtered list
    """
    filtered = []

    for result in results:
        try:
            year = int(result.get('year', 0))
            if start_year and year < start_year:
                continue
            if end_year and year > end_year:
                continue
            filtered.append(result)
        except (ValueError, TypeError):
            # Include if year parsing fails
            filtered.append(result)

    return filtered


def generate_search_summary(results: List[Dict]) -> Dict:
    """
    Generate summary statistics for search results.

    Args:
        results: List of search results

    Returns:
        Summary dictionary
    """
    summary = {
        'total_results': len(results),
        'sources': {},
        'year_distribution': {},
        'avg_citations': 0,
        'total_citations': 0,
    }

    citations = []

    for result in results:
        # Count by source
        source = result.get('source', 'Unknown')
        summary['sources'][source] = summary['sources'].get(source, 0) + 1

        # Count by year
        year = result.get('year', 'Unknown')
        summary['year_distribution'][year] = summary['year_distribution'].get(year, 0) + 1

        # Collect citations
        if result.get('citations'):
            try:
                citations.append(int(result['citations']))
            except (ValueError, TypeError):
                pass

    if citations:
        summary['avg_citations'] = sum(citations) / len(citations)
        summary['total_citations'] = sum(citations)

    return summary


def main():
    """Command-line interface for search result processing."""
    parser = argparse.ArgumentParser(description="Process and format literature search results")
    parser.add_argument("results_file")
    parser.add_argument("--format", choices=["json", "markdown", "bibtex"], default="markdown")
    parser.add_argument("--output")
    parser.add_argument("--rank", choices=["citations", "year", "relevance"])
    parser.add_argument("--year-start", type=int)
    parser.add_argument("--year-end", type=int)
    parser.add_argument("--deduplicate", action="store_true")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()

    # Load results
    try:
        with open(args.results_file, 'r', encoding='utf-8') as f:
            results = json.load(f)
    except Exception as e:
        print(f"Error loading results: {e}")
        raise SystemExit(1)

    # Process results
    if args.deduplicate:
        results = deduplicate_results(results)
        print(f"After deduplication: {len(results)} results")

    if args.year_start or args.year_end:
        results = filter_by_year(results, args.year_start, args.year_end)
        print(f"After year filter: {len(results)} results")

    if args.rank:
        results = rank_results(results, args.rank)
        print(f"Ranked by: {args.rank}")

    # Show summary
    if args.summary:
        summary = generate_search_summary(results)
        print("\n" + "=" * 60)
        print("SEARCH SUMMARY")
        print("=" * 60)
        print(json.dumps(summary, indent=2))
        print()

    # Format output
    output = format_search_results(results, args.format)

    # Write output
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(output)
        print(f"✓ Results saved to: {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()
