# Painel de dengue PE em container, no padrao da VM dos paineis Cenarios
# (painel.cenarios.unb.br/cenarios/denguepe/). O app roda na RAIZ do container
# ("jeito B" do guia): o nginx corta o prefixo /cenarios/denguepe/ e o painel
# so usa caminhos relativos (static/, geo/).
FROM python:3.13-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["python", "-m", "shiny", "run", "app.py", "--host", "0.0.0.0", "--port", "8000"]
