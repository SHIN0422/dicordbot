# 1. 베이스 이미지 선택 (파이썬 3.11 버전)
FROM python:3.11-slim

# 2. 시스템 패키지 업데이트 및 ffmpeg 설치
RUN apt-get update && apt-get install -y ffmpeg

# 3. 작업 디렉토리 설정
WORKDIR /app

# 4. requirements.txt 복사 및 파이썬 라이브러리 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --upgrade yt-dlp

# 5. 프로젝트의 모든 파일을 작업 디렉토리로 복사
COPY . .

# 6. 봇 실행 명령어 설정

CMD ["python", "bot7.py"]
