# External Imports
from discord import app_commands
from datetime import datetime
import subprocess
import discord
import asyncio
import random
import shutil
import json
import sys
import os
import re
import yt_dlp as youtube_dl
import hashlib
import pathlib
from collections import deque
from typing import Optional
import threading
from concurrent.futures import ThreadPoolExecutor

# Internal Imports
from imports.functions import *
from imports.global_setup import bot, config

# Some variables
max_file_size = int(config['files']['max_file_size']) * 1024 * 1024
telemetry_file_path = config['telemetry']['file_path']
telemetry_enabled = config['telemetry']['enabled']
admin_ids = config['settings']['admin_ids']
downloads_folder = 'downloads'
if not os.path.exists(downloads_folder):
    os.makedirs(downloads_folder)

# Add this class after imports
class GradualVolumeTransformer(discord.PCMVolumeTransformer):
    """Custom volume transformer with smooth volume transitions."""
    def __init__(self, original, volume=1.0):
        super().__init__(original, volume)
        self.target_volume = volume
        self.current_volume = volume
        self.step_size = 0.01  # How much to change volume per update
        self.update_interval = 0.05  # Time between updates in seconds

    async def update_volume(self):
        """Gradually update volume until target is reached."""
        while abs(self.current_volume - self.target_volume) > self.step_size:
            if self.current_volume < self.target_volume:
                self.current_volume = min(self.target_volume, self.current_volume + self.step_size)
            else:
                self.current_volume = max(self.target_volume, self.current_volume - self.step_size)
            
            self._volume = self.current_volume
            await asyncio.sleep(self.update_interval)

    def set_volume(self, value):
        """Set target volume and start transition if needed."""
        self.target_volume = value
        if value > 1.0:  # Only do gradual change when going above 100%
            self.step_size = 0.01  # Smaller steps for higher volumes
            asyncio.create_task(self.update_volume())
        else:
            self.current_volume = value
            self._volume = value

# Add these after other imports
class MusicQueue:
    def __init__(self):
        self.queue = deque()
        self._current_playing: Optional[str] = None

    def add(self, url: str, title: str):
        self.queue.append((url, title))

    def get_next(self) -> Optional[tuple[str, str]]:
        if self.queue:
            return self.queue.popleft()
        return None

    def clear(self):
        self.queue.clear()
        self._current_playing = None

    @property
    def is_empty(self) -> bool:
        return len(self.queue) == 0

    @property
    def current_playing(self) -> Optional[str]:
        return self._current_playing

    @current_playing.setter
    def current_playing(self, title: str):
        self._current_playing = title

# Add this after other variables
music_queues = {}  # Dictionary to store queues for each guild

# Add this function to handle playing the next song in queue
async def play_next(guild_id: int, voice_client: discord.VoiceClient):
    if guild_id not in music_queues:
        return

    queue = music_queues[guild_id]
    if queue.is_empty:
        queue.current_playing = None
        return

    next_song = queue.get_next()
    if next_song:
        url, title = next_song
        ffmpeg_options = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
            'options': '-vn'
        }

        audio_source = discord.FFmpegPCMAudio(url, **ffmpeg_options)
        transformer = GradualVolumeTransformer(audio_source, volume=1.0)
        
        def after_playing(error):
            asyncio.run_coroutine_threadsafe(play_next(guild_id, voice_client), voice_client.loop)

        voice_client.play(transformer, after=after_playing)
        voice_client.source = transformer
        queue.current_playing = title

# Add this function to handle playlist extraction
async def extract_playlist_info(url: str, ydl_opts: dict) -> list[tuple[str, str]]:
    """Extract all video URLs and titles from a playlist."""
    try:
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            info = await asyncio.get_event_loop().run_in_executor(
                None, lambda: ydl.extract_info(url, download=False)
            )
            
            if 'entries' in info:
                # This is a playlist
                return [(entry['url'], entry['title']) for entry in info['entries']]
            else:
                # This is a single video
                return [(info['url'], info['title'])]
    except Exception as e:
        print(f"Error extracting playlist: {e}")
        return []

# Async Functions
@bot.tree.command(name="help", description="Displays the help message with available commands.")
async def send_help(ctx: discord.Interaction):
    embed = discord.Embed(title="Bot Help", description="Here are the commands you can use:", color=0x00ff00)
    embed.add_field(name="/join", value="Joins voice channel that you're currently in.", inline=False)
    embed.add_field(name="/leave", value="Leaves the voice channel.", inline=False)
    embed.add_field(name="/play <url>", value="Adds a song to the queue", inline=False)
    embed.add_field(name="/forceplay <url>", value="Forces a song to play immediately", inline=False)
    embed.add_field(name="/skip", value="Skips the currently playing song", inline=False)
    embed.add_field(name="/queue", value="Shows the current music queue", inline=False)
    embed.add_field(name="/stop", value="Stops the currently playing audio.", inline=False)
    embed.add_field(name="/volume <0-200>", value="Set the volume (0-200%)", inline=False)
    if str(ctx.user.id) in admin_ids:
        embed.add_field(name="/clearcache", value="Clears the audio cache (Admin only)", inline=False)
    await ctx.response.send_message(embed=embed)

@bot.tree.command(name="join", description="Joins the voice channel you are currently in.")
async def join_voice_channel(ctx: discord.Interaction):
    # Check if the user is in a voice channel
    if ctx.user.voice:
        channel = ctx.user.voice.channel
        await channel.connect()
        await ctx.response.send_message(f"Joined <#{channel.id}>!")
    else:
        await ctx.response.send_message("You are not in a voice channel.")

@bot.tree.command(name="leave", description="Leaves the voice channel.")
async def leave_voice_channel(ctx: discord.Interaction):
    voice_client = ctx.guild.voice_client
    if voice_client:
        # Clear the queue when leaving
        if ctx.guild.id in music_queues:
            music_queues[ctx.guild.id].clear()
        await voice_client.disconnect()
        await ctx.response.send_message("Left the voice channel.")
    else:
        await ctx.response.send_message("I am not in a voice channel.")

@bot.tree.command(name="play", description="Adds song(s) to the queue (supports playlists)")
async def play(ctx: discord.Interaction, url: str):
    await ctx.response.defer(thinking=True)

    if not ctx.user.voice:
        await ctx.followup.send("You are not in a voice channel.")
        return

    channel = ctx.user.voice.channel
    voice_client = ctx.guild.voice_client

    if voice_client is None:
        voice_client = await channel.connect()

    # Initialize queue if it doesn't exist
    if ctx.guild.id not in music_queues:
        music_queues[ctx.guild.id] = MusicQueue()

    queue = music_queues[ctx.guild.id]

    try:
        ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'extract_flat': 'in_playlist'  # Don't download playlist videos immediately
        }

        # Extract playlist info in a separate thread
        tracks = await extract_playlist_info(url, ydl_opts)
        
        if not tracks:
            await ctx.followup.send("No tracks found in the URL.")
            return

        is_playlist = len(tracks) > 1
        if is_playlist:
            await ctx.followup.send(f"Adding {len(tracks)} tracks to queue...")

        first_track = True
        for track_url, track_title in tracks:
            if voice_client.is_playing() or not first_track:
                # Add to queue
                queue.add(track_url, track_title)
                if not is_playlist:  # Only send message for single tracks
                    await ctx.followup.send(f"Added to queue: {track_title}")
            else:
                # Play first track immediately
                ffmpeg_options = {
                    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
                    'options': '-vn'
                }

                audio_source = discord.FFmpegPCMAudio(track_url, **ffmpeg_options)
                transformer = GradualVolumeTransformer(audio_source, volume=1.0)
                
                def after_playing(error):
                    if error:
                        print(f"Error in playback: {error}")
                    asyncio.run_coroutine_threadsafe(play_next(ctx.guild.id, voice_client), voice_client.loop)

                voice_client.play(transformer, after=after_playing)
                voice_client.source = transformer
                queue.current_playing = track_title
                if not is_playlist:  # Only send message for single tracks
                    await ctx.followup.send(f"Now playing: {track_title}")
            
            first_track = False

        if is_playlist:
            await ctx.followup.send(f"Successfully added playlist to queue! Use /queue to see the full list.")

    except Exception as e:
        print(f"Error playing audio: {e}")
        await ctx.followup.send("There was an error trying to play the audio.")

@bot.tree.command(name="stop", description="Stops the currently playing audio.")
async def stop(ctx: discord.Interaction):
    voice_client = ctx.guild.voice_client
    if voice_client and voice_client.is_playing():
        voice_client.stop()
        await ctx.response.send_message("Stopped playing audio.")
    else:
        await ctx.response.send_message("Nothing is currently playing.")

@bot.tree.command(name="clearcache", description="Clears the audio cache (Admin only)")
async def clearcache(ctx: discord.Interaction):
    if str(ctx.user.id) in admin_ids:
        try:
            files = os.listdir(downloads_folder)
            for file in files:
                file_path = os.path.join(downloads_folder, file)
                try:
                    os.remove(file_path)
                except Exception as e:
                    print(f"Error removing {file}: {e}")
            
            await ctx.response.send_message("Cache cleared successfully!")
        except Exception as e:
            await ctx.response.send_message(f"Error clearing cache: {e}")
    else:
        await ctx.response.send_message("You don't have permission to use this command.")

@bot.tree.command(name="volume", description="Set the volume (0-200%)")
async def volume(ctx: discord.Interaction, percentage: int):
    if not 0 <= percentage <= 200:
        await ctx.response.send_message("Volume must be between 0% and 200%")
        return

    voice_client = ctx.guild.voice_client
    if voice_client and voice_client.source:
        volume = percentage / 100
        voice_client.source.set_volume(volume)
        await ctx.response.send_message(f"Volume set to {percentage}%")
    else:
        await ctx.response.send_message("Nothing is playing right now")

# Add the forceplay command
@bot.tree.command(name="forceplay", description="Forces a song to play immediately, stopping the current song")
async def forceplay(ctx: discord.Interaction, url: str):
    await ctx.response.defer(thinking=True)

    if not ctx.user.voice:
        await ctx.followup.send("You are not in a voice channel.")
        return

    channel = ctx.user.voice.channel
    voice_client = ctx.guild.voice_client

    if voice_client is None:
        voice_client = await channel.connect()

    try:
        ydl_opts = {
            'format': 'bestaudio/best',
            'noplaylist': True,
            'quiet': True,
        }

        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info.get('url', None) is None:
                info = ydl.extract_info(url, download=False, process=True)
            
            if 'entries' in info:
                url = info['entries'][0]['url']
                title = info['entries'][0]['title']
            else:
                url = info['url']
                title = info['title']

        if voice_client.is_playing():
            voice_client.stop()

        ffmpeg_options = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
            'options': '-vn'
        }

        audio_source = discord.FFmpegPCMAudio(url, **ffmpeg_options)
        transformer = GradualVolumeTransformer(audio_source, volume=1.0)
        
        def after_playing(error):
            asyncio.run_coroutine_threadsafe(play_next(ctx.guild.id, voice_client), voice_client.loop)

        voice_client.play(transformer, after=after_playing)
        voice_client.source = transformer

        # Update current playing in queue
        if ctx.guild.id in music_queues:
            music_queues[ctx.guild.id].current_playing = title

        await ctx.followup.send(f"Now playing: {title} (Queue will continue after this song)")

    except Exception as e:
        print(f"Error playing audio: {e}")
        await ctx.followup.send("There was an error trying to play the audio.")

# Add the skip command
@bot.tree.command(name="skip", description="Skips the currently playing song")
async def skip(ctx: discord.Interaction):
    voice_client = ctx.guild.voice_client
    if not voice_client:
        await ctx.response.send_message("I am not in a voice channel.")
        return

    if not voice_client.is_playing():
        await ctx.response.send_message("Nothing is currently playing.")
        return

    # Get queue for this guild
    queue = music_queues.get(ctx.guild.id)
    if queue and not queue.is_empty:
        # If there are songs in queue, stop current song (this will trigger play_next)
        voice_client.stop()
        await ctx.response.send_message("Skipped! Playing next song in queue...")
    else:
        # If no songs in queue, just stop
        voice_client.stop()
        await ctx.response.send_message("Skipped! No more songs in queue.")

# Add the queue command
@bot.tree.command(name="queue", description="Shows the current music queue")
async def queue(ctx: discord.Interaction):
    queue = music_queues.get(ctx.guild.id)
    
    if not queue or (queue.is_empty and not queue.current_playing):
        await ctx.response.send_message("The queue is empty and nothing is playing.")
        return

    # Create an embed to display the queue
    embed = discord.Embed(title="Music Queue", color=0x00ff00)
    
    # Add currently playing song
    if queue.current_playing:
        embed.add_field(name="Now Playing", value=queue.current_playing, inline=False)
    
    # Add queued songs
    if not queue.is_empty:
        queue_list = []
        for i, (_, title) in enumerate(queue.queue, 1):
            queue_list.append(f"{i}. {title}")
        
        queue_text = "\n".join(queue_list)
        # If queue is too long, truncate it
        if len(queue_text) > 1024:
            queue_text = queue_text[:1021] + "..."
        
        embed.add_field(name="Up Next", value=queue_text, inline=False)
    
    # Add total count
    embed.set_footer(text=f"Total songs in queue: {len(queue.queue)}")
    
    await ctx.response.send_message(embed=embed)

# Functions
def restart():
    try:
        print("Bot is restarting...")
        os.execv(sys.executable, ['python'] + sys.argv)
        
    except Exception as e:
        print(f"Error during bot restart: {e}")

def remove_pycache():
    for root, dirs, files in os.walk('.'):
        for dir in dirs:
            if dir == '__pycache__':
                shutil.rmtree(os.path.join(root, dir))

def check_files(config, base_path=''):
    missing_files = []

    def scan_config(d, path_prefix=''):
        for key, value in d.items():
            if isinstance(value, dict):
                # Recursively scan nested dictionaries
                scan_config(value, path_prefix)
            elif isinstance(value, str) and ('.' in value or '/' in value or '\\' in value):
                # Check if the value looks like a file path
                file_path = os.path.join(base_path, value)
                if not os.path.exists(file_path):
                    missing_files.append(file_path)

    scan_config(config)

    if missing_files:
        print("\nMissing files:")

        for file in missing_files:
            print(f"  {file}")

        exit()
    else:
        print("\nAll required files are present.")

def log_telemetry(message):
    if telemetry_enabled:
        timestamp = datetime.now().isoformat()
        log_entry = {timestamp: message}
        
        if os.path.exists(telemetry_file_path):
            with open(telemetry_file_path, 'r', encoding='utf-8') as file:
                telemetry_data = json.load(file)
        else:
            telemetry_data = {"telemetry": {}}
        
        telemetry_data["telemetry"].update(log_entry)
        
        with open(telemetry_file_path, 'w', encoding='utf-8') as file:
            json.dump(telemetry_data, file, indent=4, ensure_ascii=False)

def print_and_log(message):
    print(message)
    log_telemetry(message)

def get_cached_file(video_id):
    """Check if a video is already cached and return its path if it exists."""
    cache_path = os.path.join(downloads_folder, f"{video_id}.mp3")
    return cache_path if os.path.exists(cache_path) else None

def get_video_id(url):
    """Extract video ID from YouTube URL."""
    try:
        with youtube_dl.YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(url, download=False)
            return info.get('id', None)
    except:
        return None