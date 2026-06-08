#!/usr/bin/env python3
"""Run the Canada Job Bank scraper only."""

from scraper.job_bank import scrape_job_bank_jobs

if __name__ == "__main__":
    scrape_job_bank_jobs()
