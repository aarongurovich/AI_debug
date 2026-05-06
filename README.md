# AI Debug

AI Debug is a tool to help find and analyze bugs in code automatically. It works with multiple languages including Python, Java, JavaScript, and C.

## Features

- Analyze code for bugs
- Run benchmarks on sample code
- Generate visualizations of debugging results
- Works with frontend and backend components

## Requirements

- Python 3.10+
- Node.js and npm (for frontend)
- Deno (for Supabase functions)
- Git

## Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/aarongurovich/AI_debug.git
   cd AI_debug
   ```

2. **Backend setup (Python scripts):**
   - Navigate to the data pipeline folder:
     ```bash
     cd data_pipeline
     ```
   - Install Python dependencies if needed (example):
     ```bash
     pip install -r requirements.txt
     ```
   - Run the scraper or main script:
     ```bash
     python scraper_daily.py
     python script.py
     ```

3. **Frontend setup (React app):**
   - Navigate to the frontend folder:
     ```bash
     cd frontend
     ```
   - Install npm packages:
     ```bash
     npm install
     ```
   - Start the frontend:
     ```bash
     npm run dev
     ```
   - Open `http://localhost:5173` in your browser.

4. **Supabase Functions:**
   - Navigate to the Supabase function:
     ```bash
     cd supabase/functions/generate-fix
     ```
   - Deploy with Supabase CLI:
     ```bash
     supabase functions deploy generate-fix
     ```

5. **Testing and Benchmarks:**
   - Go to the testing folder to run manual benchmarks or view dashboard:
     ```bash
     cd testing
     python script.py
     ```
   - Benchmark results are saved in `benchmark_dashboard/dashboard_results.csv` and images are in `benchmark_dashboard/`.

## Usage

- Place code you want to debug in the `testing/manual_benchmarking` folder under the appropriate language folder.
- Run the Python scripts to analyze the code.
- Check results in the frontend dashboard or CSV files.

## Notes

- Make sure Python, Node.js, and Deno are installed before running scripts.
- Frontend and backend can run separately.
- CSV and image outputs are generated automatically in `testing/benchmark_dashboard`.
