import os
import asyncio
import logging
import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp as youtube_dl

# [중요] 테스트할 서버 ID를 여기에 입력하세요.
# 서버 ID는 서버 아이콘 우클릭 -> '서버 ID 복사'로 얻을 수 있습니다. (개발자 모드 활성화 필요)
logging.basicConfig(level=logging.INFO)

# 환경변수에서 GUILD ID와 TOKEN 불러오기 (없으면 기본값 사용)
TEST_GUILD_ID = int(os.getenv("TEST_GUILD_ID", "1317122769942478878"))
GUILD_OBJECT = discord.Object(id=TEST_GUILD_ID)
TOKEN = os.getenv("DISCORD_TOKEN")


# --- YTDL 및 FFmpeg 설정 (기존과 동일) ---
youtube_dl.utils.bug_reports_message = lambda *args, **kwargs: ''

ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
}

ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=True):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))

        if 'entries' in data:
            data = data['entries'][0]

        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)

# --- Music Cog (모든 버그가 수정된 최종 버전) ---
class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.song_queue = {}
        self.now_playing = {}
        self.repeat_mode = {}
        self.is_skipping = {} # 스킵 여부 추적

    async def play_next(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        voice_client = interaction.guild.voice_client
        if not voice_client:
            await interaction.response.send_message("❌ 먼저 /입장 으로 음성 채널에 들어가 주세요.", ephemeral=True)
            return

        # 스킵으로 호출된 경우, 반복 로직을 실행하지 않음
        if self.is_skipping.get(guild_id):
            self.is_skipping[guild_id] = False
        
        # 노래가 자연스럽게 끝난 경우에만 반복 로직 실행
        else:
            last_song = self.now_playing.get(guild_id)
            mode = self.repeat_mode.get(guild_id, "off")

            if last_song and mode != "off":
                try:
                    # [핵심] 재사용 불가능한 스트림 대신 새로운 스트림을 생성
                    fresh_player = await YTDLSource.from_url(last_song.data['webpage_url'], loop=self.bot.loop, stream=True)
                    if mode == "one":
                        self.song_queue.setdefault(guild_id, []).insert(0, fresh_player)
                    elif mode == "all":
                        self.song_queue.setdefault(guild_id, []).append(fresh_player)
                except Exception as e:
                    await interaction.channel.send(f"⚠️ 반복 재생 중 오류가 발생했습니다: {e}")

        queue = self.song_queue.get(guild_id)
        if queue:
            song = queue.pop(0)
            callback = lambda e: asyncio.run_coroutine_threadsafe(self.play_next(interaction), self.bot.loop)
            voice_client.play(song, after=callback)
            self.now_playing[guild_id] = song
            await interaction.channel.send(f"🎵 **재생 시작:** {song.title}")
        else:
            self.now_playing[guild_id] = None
            await interaction.channel.send("✅ 모든 대기열 재생이 끝났습니다.")

    @app_commands.command(name="입장", description="봇을 현재 음성 채널에 참여시킵니다.")
    @app_commands.guilds(GUILD_OBJECT)
    async def join(self, interaction: discord.Interaction):
        if interaction.user.voice:
            if interaction.guild.voice_client:
                return await interaction.response.send_message("이미 음성 채널에 있습니다!", ephemeral=True)
            await interaction.user.voice.channel.connect()
            await interaction.response.send_message(f"`{interaction.user.voice.channel}` 채널에 참여했습니다.")
        else:
            await interaction.response.send_message("먼저 음성 채널에 들어가주세요!", ephemeral=True)
            
    @app_commands.command(name="재생", description="노래를 재생하거나 대기열에 추가합니다.")
    @app_commands.describe(url="재생할 노래의 YouTube URL 또는 검색어")
    @app_commands.guilds(GUILD_OBJECT)
    async def play(self, interaction: discord.Interaction, url: str):
        if interaction.user.voice is None:
            await interaction.response.send_message("먼저 음성 채널에 들어가주세요!", ephemeral=True)
            return
        
        # [핵심] 시간이 오래 걸리는 작업 전에 defer()를 호출해 3초 제한을 피합니다.
        await interaction.response.defer(ephemeral=True)

        vc = interaction.guild.voice_client
        if not vc:
            vc = await interaction.user.voice.channel.connect()

        guild_id = interaction.guild.id
        try:
            player = await YTDLSource.from_url(url, loop=self.bot.loop, stream=True)
            self.song_queue.setdefault(guild_id, []).append(player)
        except Exception as e:
            await interaction.followup.send(f"❌ 노래를 찾는 중 오류가 발생했습니다: `{e}`", ephemeral=True)
            return

        if not vc.is_playing():
            await interaction.followup.send(f"▶️ 재생을 시작합니다!")
            await self.play_next(interaction)
        else:
            await interaction.followup.send(f"✅ **대기열 추가:** {player.title}")

    @app_commands.command(name="반복", description="노래 반복 모드를 설정합니다.")
    @app_commands.describe(모드="설정할 반복 모드를 선택하세요.")
    @app_commands.choices(모드=[
        app_commands.Choice(name="끄기", value="off"),
        app_commands.Choice(name="한 곡 반복", value="one"),
        app_commands.Choice(name="전체 반복", value="all"),
    ])
    @app_commands.guilds(GUILD_OBJECT)
    async def repeat(self, interaction: discord.Interaction, 모드: app_commands.Choice[str]):
        guild_id = interaction.guild.id
        self.repeat_mode[guild_id] = 모드.value
        await interaction.response.send_message(f"🔁 반복 모드를 **{모드.name}**(으)로 설정했습니다.")
    
    @app_commands.command(name="스킵", description="현재 재생 중인 노래를 건너뜁니다.")
    @app_commands.guilds(GUILD_OBJECT)
    async def skip(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            self.is_skipping[interaction.guild.id] = True # 스킵 상태임을 표시
            vc.stop()
            await interaction.response.send_message("⏭️ 현재 노래를 건너뛰었습니다.")
        else:
            await interaction.response.send_message("재생 중인 노래가 없습니다.", ephemeral=True)

    @app_commands.command(name="정지", description="음악 재생을 멈추고 대기열을 비웁니다.")
    @app_commands.guilds(GUILD_OBJECT)
    async def stop(self, interaction: discord.Interaction):
        if interaction.guild.voice_client:
            guild_id = interaction.guild.id
            self.song_queue.setdefault(guild_id, []).clear()
            self.repeat_mode[guild_id] = "off"
            self.is_skipping[guild_id] = False
            interaction.guild.voice_client.stop()
            await interaction.guild.voice_client.disconnect()
            await interaction.response.send_message("⏹️ 재생을 멈추고 모든 설정을 초기화했습니다.")

    @app_commands.command(name="일시정지", description="현재 음악을 일시정지합니다.")
    @app_commands.guilds(GUILD_OBJECT)
    async def pause(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.pause()
            await interaction.response.send_message("⏸️ 음악을 일시정지했습니다.")
        else:
            await interaction.response.send_message("재생 중인 음악이 없습니다.", ephemeral=True)
            
    @app_commands.command(name="다시재생", description="일시정지된 음악을 다시 재생합니다.")
    @app_commands.guilds(GUILD_OBJECT)
    async def resume(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_paused():
            vc.resume()
            await interaction.response.send_message("▶️ 음악을 다시 재생합니다.")
        else:
            await interaction.response.send_message("일시정지된 음악이 없습니다.", ephemeral=True)

    @app_commands.command(name="볼륨", description="봇의 볼륨을 조절합니다 (0-100).")
    @app_commands.describe(크기="설정할 볼륨 크기를 0에서 100 사이로 입력하세요.")
    @app_commands.guilds(GUILD_OBJECT)
    async def volume(self, interaction: discord.Interaction, 크기: int):
        vc = interaction.guild.voice_client
        if not vc or not vc.source:
            return await interaction.response.send_message("재생 중인 음악이 없습니다.", ephemeral=True)
        
        if not 0 <= 크기 <= 100:
            return await interaction.response.send_message("볼륨은 0에서 100 사이로 설정해주세요.", ephemeral=True)

        vc.source.volume = 크기 / 100
        await interaction.response.send_message(f"🔊 볼륨을 {크기}%로 설정했습니다.")
        
    @app_commands.command(name="대기열", description="현재 대기열에 있는 노래 목록을 보여줍니다.")
    @app_commands.guilds(GUILD_OBJECT)
    async def queue(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        queue = self.song_queue.get(guild_id, [])
        if queue or self.now_playing.get(guild_id):
            embed = discord.Embed(title="🎶 노래 대기열", color=discord.Color.blue())
            
            if self.now_playing.get(guild_id):
                 embed.add_field(name="현재 재생 중", value=f"**{self.now_playing[guild_id].title}**", inline=False)
            
            if queue:
                queue_list = "\n".join([f"{i+1}. {s.title}" for i, s in enumerate(queue[:10])])
                embed.add_field(name="다음 곡 목록", value=queue_list, inline=False)
            
            if len(queue) > 10:
                embed.set_footer(text=f"... 외 {len(queue) - 10}곡 더")

            current_mode = self.repeat_mode.get(guild_id, "off")
            mode_text = {"off": "끄기", "one": "한 곡 반복", "all": "전체 반복"}
            embed.set_author(name=f"반복 모드: {mode_text[current_mode]}")

            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message("현재 대기열이 비어있습니다.")

# ---------------------- 봇 초기화 (슬래시 커맨드 방식) ----------------------
class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix='/', # 접두사는 더 이상 필요 없지만 형식상 유지
            intents=discord.Intents.all()
        )
    
    # [중요] 봇이 처음 시작될 때 Cog를 로드하고 커맨드를 동기화하는 부분
    async def setup_hook(self):
        await self.add_cog(Music(self))
        # 특정 길드에만 커맨드를 동기화 (테스트 시 매우 빠름)
        self.tree.copy_global_to(guild=GUILD_OBJECT)
        await self.tree.sync(guild=GUILD_OBJECT)
        # 만약 모든 서버에 적용하려면 위 두 줄을 아래 한 줄로 대체 (최대 1시간 소요)
        # await self.tree.sync()

    async def on_ready(self):
        print(f'{self.user} (ID: {self.user.id}) 가 준비되었습니다.')
        print('='*20)

bot = MyBot()

# keep-alive 서버 (Replit) 를 import해서 실행 (keep_alive.py가 필요)
try:
    from keep_alive import keep_alive
    keep_alive()
except Exception:
    pass

if not TOKEN:
    logging.error("DISCORD_TOKEN 환경변수가 설정되어 있지 않습니다.")
else:
    bot.run(TOKEN)
