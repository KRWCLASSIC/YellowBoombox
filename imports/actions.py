# External Imports
import discord
import re
import asyncio  # Import asyncio for sleep functionality

# Internal Imports
from imports.functions import *
from imports.global_setup import bot, ver, config

# Some variables and arrays
admin_ids = config['settings']['admin_ids']

async def on_ready():
    await bot.tree.sync()
    # Print the ASCII art
    print('''\
__   __   _ _               ____                        _               
\ \ / /__| | | _____      _| __ )  ___   ___  _ __ ___ | |__   _____  __
 \ V / _ \ | |/ _ \ \ /\ / /  _ \ / _ \ / _ \| '_ ` _ \| '_ \ / _ \ \/ /
  | |  __/ | | (_) \ V  V /| |_) | (_) | (_) | | | | | | |_) | (_) >  < 
  |_|\___|_|_|\___/ \_/\_/ |____/ \___/ \___/|_| |_| |_|_.__/ \___/_/\_\\''')
    
    print(f'Bot is online! Logged in as {bot.user}')
    print(f'Yellow Boombox ver. {ver}')
    for guild in bot.guilds:
        print(f'- {guild.name}')

async def on_member_join(member):
    return

async def on_reaction_add(reaction, user):
    if reaction.message.author == bot.user:
        return
    return

async def on_member_update(before: discord.Member, after: discord.Member):
    return

async def on_message(message):
    if message.author == bot.user:
        return

async def on_voice_state_update(member, before, after):
    # Check if the bot is in a voice channel
    if member.guild.voice_client and member.guild.voice_client.channel:
        # Check if the bot is alone in the voice channel
        if len(member.guild.voice_client.channel.members) == 1:  # Only the bot is present
            await asyncio.sleep(10)  # Wait for 10 seconds
            # Check again if the bot is still alone
            if len(member.guild.voice_client.channel.members) == 1:
                await member.guild.voice_client.disconnect()
                print(f"Left the voice channel due to inactivity.")