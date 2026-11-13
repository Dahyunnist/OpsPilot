FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py ./app.py
COPY time_window.py ./time_window.py
EXPOSE 8080
CMD ["python", "app.py"]
