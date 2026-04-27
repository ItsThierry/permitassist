# 100-city benchmark scaffold
import json
cities = [f'City-{i}' for i in range(1,101)]
models = ['openai/gpt-4.1','google/gemini-3.1-pro','claude/opus']
results = []
for city in cities:
    results.append({'city': city, 'scores': {m: {'accuracy': None, 'specificity': None, 'hallucination': None} for m in models}})
print(json.dumps(results[:5], indent=2))
