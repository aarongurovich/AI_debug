import os
import time
import random
import re
import asyncio
import aiohttp
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client
load_dotenv()

# --- Credentials ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# --- Configuration ---
ENDPOINT_URL = f"{SUPABASE_URL}/functions/v1/generate-fix" 
OUTPUT_DIR = "benchmark_dashboard"
TIMEOUT_SECONDS = 30  
TEST_COUNT = 100 
MAX_RETRIES = 3
RATE_LIMIT_DELAY = 4.1 # Pacing to hit ~14.6 RPM

TARGET_LANGUAGES = ["python", "java", "javascript", "c"]
HEADERS = {"Content-Type": "application/json", "Authorization": f"Bearer {SUPABASE_KEY}"}

print("Initializing Supabase client...")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def normalize_url(url):
    if not url: return ""
    return str(url).lower().strip().replace("https://", "").replace("http://", "").replace("www.", "").rstrip('/')

def perturb_error_message(text, lang):
    """Chaos Mutation Engine: Returns (mutated_text, mutation_category)."""
    if not isinstance(text, str) or len(text) < 10: return text, "None"
    mutations = {
        "Slang_Wrapper": lambda t: random.choice(["wtf: ", "help: ", "broken: "]) + t,
        "No_Punctuation": lambda t: re.sub(r'[^\w\s]', ' ', t),
        "Variable_Redaction": lambda t: re.sub(r"['\"`].*?['\"`]", 'var', t),
        "Truncation_50%": lambda t: t[:len(t)//2],
        "Typo_Injection": lambda t: t.replace('error', 'errrr').replace('null', 'nulll').replace('function', 'func'),
        "Terminal_Noise": lambda t: f"admin@server:~$ {t}\n[process exited]"
    }
    m_type = random.choice(list(mutations.keys()))
    return mutations[m_type](text).strip().lower(), m_type

async def fetch_benchmark(session, idx, total, true_lang, target_norm, fuzzed_msg, m_type):
    # Stagger requests to hit exactly the RPM limit without waiting on network latency
    await asyncio.sleep(idx * RATE_LIMIT_DELAY)
    
    start_time = time.time()
    
    # Implemented your MAX_RETRIES logic
    for attempt in range(MAX_RETRIES):
        try:
            async with session.post(ENDPOINT_URL, json={"language": true_lang, "errorMessage": fuzzed_msg}, timeout=TIMEOUT_SECONDS) as res:
                latency = time.time() - start_time
                
                if res.status == 200:
                    data = await res.json()
                    sources = data.get("sources", [])
                    solution = data.get("solution", "")
                    
                    found_rank = next((r + 1 for r, s in enumerate(sources) if normalize_url(s) == target_norm), None)
                    mrr = (1.0 / found_rank) if found_rank else 0.0
                    
                    print(f"[{idx+1:03d}/{total}] {true_lang.upper():<10} | {m_type:<18} | HIT (R{found_rank if found_rank else 'X'}) | {latency:.2f}s")
                    
                    return {
                        "Language": true_lang.upper(),
                        "Mutation": m_type,
                        "Hit": found_rank is not None,
                        "Rank": found_rank,
                        "MRR": mrr,
                        "Latency": latency,
                        "Query_Len": len(fuzzed_msg),
                        "Sol_Len": len(solution),
                        "Recall@1": 1 if found_rank == 1 else 0,
                        "Recall@3": 1 if found_rank and found_rank <= 3 else 0
                    }
                elif res.status == 429:
                    print(f"[{idx+1:03d}/{total}] Rate Limited! Retrying in 5s...")
                    await asyncio.sleep(5) # Backoff if we slip past the Gemini limit
                else:
                    print(f"[{idx+1:03d}/{total}] FAIL ({res.status})")
                    break
        except Exception as e:
            if attempt == MAX_RETRIES - 1:
                print(f"[{idx+1:03d}/{total}] CRASH: {str(e)}")
            await asyncio.sleep(2)
            
    return None

async def run_benchmarks_async():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Fetching balanced data synchronously (this is fast enough)
    response = supabase.table("solutions").select("language, error_message, source_url").limit(3000).execute()
    df = pd.DataFrame(response.data).dropna()
    df['language'] = df['language'].str.lower().str.strip()
    
    limit_per_lang = TEST_COUNT // len(TARGET_LANGUAGES)
    df_tests = pd.concat([df[df['language'] == l].sample(n=min(limit_per_lang, len(df[df['language'] == l]))) for l in TARGET_LANGUAGES])
    df_tests = df_tests.sample(frac=1).reset_index(drop=True)

    print(f"\n--- STARTING FULL DASHBOARD BENCHMARK ---")
    
    tasks = []
    total_tests = len(df_tests)
    
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        for idx, row in df_tests.iterrows():
            true_lang = str(row['language']).strip()
            target_norm = normalize_url(str(row['source_url']))
            fuzzed_msg, m_type = perturb_error_message(str(row['error_message']).strip(), true_lang)
            
            # Queue up all requests simultaneously with their staggered delays
            task = asyncio.create_task(
                fetch_benchmark(session, idx, total_tests, true_lang, target_norm, fuzzed_msg, m_type)
            )
            tasks.append(task)
            
        # Wait for all tasks to finish
        raw_results = await asyncio.gather(*tasks)
        
    # Filter out failed requests
    results = [r for r in raw_results if r is not None]

    df_res = pd.DataFrame(results)
    df_res.to_csv(os.path.join(OUTPUT_DIR, "dashboard_results.csv"), index=False)
    generate_dashboard(df_res)

def generate_dashboard(df):
    print("\n--- GENERATING DASHBOARD VISUALS ---")
    sns.set_theme(style="whitegrid", context="talk")
    df_succ = df[df["Hit"] == True]

    # ... [Keep your exact generate_dashboard logic here] ...
    # 1. MRR Heatmap (Language vs Mutation)
    plt.figure(figsize=(12, 8))
    pivot = df.pivot_table(index='Mutation', columns='Language', values='MRR', aggfunc='mean')
    sns.heatmap(pivot, annot=True, cmap="YlGnBu", fmt=".2f", cbar_kws={'label': 'MRR Score'})
    plt.title("Retrieval Quality Heatmap (MRR)")
    plt.savefig(os.path.join(OUTPUT_DIR, "01_resilience_heatmap.png"))
    plt.close() # Always close matplotlib figures to save memory

    # 2. Recall@K Funnel
    plt.figure(figsize=(8, 6))
    funnel = pd.DataFrame({
        'Level': ['Recall@1', 'Recall@3', 'Overall Hit Rate'],
        'Percentage': [df['Recall@1'].mean(), df['Recall@3'].mean(), df['Hit'].mean()]
    })
    sns.barplot(data=funnel, x='Level', y='Percentage', palette="mako")
    plt.title("Search Precision Funnel")
    plt.ylim(0, 1)
    plt.savefig(os.path.join(OUTPUT_DIR, "02_recall_funnel.png"))
    plt.close()

    # 3. Latency vs Complexity (Scatter)
    plt.figure(figsize=(10, 6))
    sns.scatterplot(data=df, x="Query_Len", y="Latency", hue="Language", alpha=0.7)
    plt.title("Latency Correlation with Query Length")
    plt.savefig(os.path.join(OUTPUT_DIR, "03_latency_scatter.png"))
    plt.close()

    # 4. Rank Distribution (Count)
    plt.figure(figsize=(8, 6))
    if not df_succ.empty:
        sns.countplot(data=df_succ, x="Rank", palette="flare")
    plt.title("Distribution of Match Ranks (Lower is Better)")
    plt.savefig(os.path.join(OUTPUT_DIR, "04_rank_distribution.png"))
    plt.close()

    # 5. Throughput Boxplot
    df['CPS'] = df['Sol_Len'] / df['Latency']
    plt.figure(figsize=(10, 6))
    sns.boxplot(data=df, x="Language", y="CPS", palette="Set3")
    plt.title("Generation Throughput (Chars/Sec) per Language")
    plt.savefig(os.path.join(OUTPUT_DIR, "05_throughput_boxplot.png"))
    plt.close()

    # 6. MRR Distribution (Violin)
    plt.figure(figsize=(10, 6))
    sns.violinplot(data=df, x="Language", y="MRR", inner="quart", palette="pastel")
    plt.title("MRR Density per Language")
    plt.savefig(os.path.join(OUTPUT_DIR, "06_mrr_density.png"))
    plt.close()

    # 7. Failure Frequency by Mutation (Misses only)
    plt.figure(figsize=(10, 6))
    df_miss = df[df["Hit"] == False]
    if not df_miss.empty:
        sns.countplot(data=df_miss, y="Mutation", palette="Reds_r")
        plt.title("Which Mutation Causes the Most Misses?")
        plt.savefig(os.path.join(OUTPUT_DIR, "07_failure_modes.png"))
    plt.close()

    # 8. Success Timeline
    plt.figure(figsize=(12, 4))
    plt.plot(df.index, df['Latency'].rolling(window=5).mean(), label="Rolling Avg Latency", color="blue")
    plt.scatter(df.index, df['Latency'], c=df['Hit'].map({True: 'green', False: 'red'}), alpha=0.5)
    plt.title("Benchmark Execution Timeline (Success vs Latency)")
    plt.xlabel("Test Sequence")
    plt.savefig(os.path.join(OUTPUT_DIR, "08_execution_timeline.png"))
    plt.close()

    print(f"8 charts successfully exported to '{OUTPUT_DIR}'")

if __name__ == "__main__":
    # Run the async loop
    asyncio.run(run_benchmarks_async())