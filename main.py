import discord
from discord.ext import commands
import asyncio
import logging
import os
from dotenv import load_dotenv
import yt_dlp
import ffmpeg
from collections import deque

# Load opus library with better error handling for Railway/hosting platforms
def load_opus_library():
    """Load opus library with multiple fallback options"""
    import ctypes.util
    
    opus_paths = [
        'opus',
        'libopus.so.0',
        'libopus.so',
        'libopus.so.0.8.0',
        'libopus.so.0.8',
        '/usr/lib/x86_64-linux-gnu/libopus.so.0',
        '/usr/lib/libopus.so.0',
        '/usr/local/lib/libopus.so.0',
        ctypes.util.find_library('opus')
    ]
    
    for opus_path in opus_paths:
        if opus_path is None:
            continue
        try:
            discord.opus.load_opus(opus_path)
            print(f"Successfully loaded opus from: {opus_path}")
            return True
        except Exception as e:
            print(f"Failed to load opus from {opus_path}: {e}")
            continue
    
    # Final attempt without specifying path
    try:
        discord.opus.load_opus()
        print("Successfully loaded opus (default)")
        return True
    except Exception as e:
        print(f"Failed to load opus library: {e}")
        return False

# Load opus
opus_loaded = load_opus_library()
if not opus_loaded:
    print("Warning: Opus library not loaded - voice functionality may not work")
else:
    print("Opus library loaded successfully")

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Bot configuration
intents = discord.Intents.default()
# Only enable necessary intents to avoid privileged intent errors
intents.message_content = False  # Not needed for slash commands

# YouTube DL configuration
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
    'options': '-vn -filter:a "volume=0.5"'
}

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

# Music queue storage
music_queues = {}

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        
        if 'entries' in data:
            data = data['entries'][0]
        
        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)

class DiscordBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix='!',  # Prefix for text commands (optional)
            intents=intents,
            help_command=None
        )
    
    async def setup_hook(self):
        """This is called when the bot is starting up"""
        try:
            # Sync slash commands with Discord
            synced = await self.tree.sync()
            logger.info(f"Synced {len(synced)} command(s)")
        except Exception as e:
            logger.error(f"Failed to sync commands: {e}")
    
    async def on_ready(self):
        """Called when the bot is ready and connected"""
        logger.info(f'Bot aktif sebagai {self.user.tag}!')
        logger.info(f'Bot ID: {self.user.id}')
        logger.info(f'Connected to {len(self.guilds)} guild(s)')
        
        # Set bot status
        await self.change_presence(
            activity=discord.Game(name="Responding to /ping commands"),
            status=discord.Status.online
        )
    
    async def on_guild_join(self, guild):
        """Called when the bot joins a new guild"""
        logger.info(f"Joined guild: {guild.name} (ID: {guild.id})")
    
    async def on_guild_remove(self, guild):
        """Called when the bot leaves a guild"""
        logger.info(f"Left guild: {guild.name} (ID: {guild.id})")
        
        # Clean up music queue for this guild
        if guild.id in music_queues:
            del music_queues[guild.id]

# Create bot instance
bot = DiscordBot()

@bot.tree.command(name="ping", description="Responds with Pong! 🏓")
async def ping(interaction: discord.Interaction):
    """Ping command that responds with Pong!"""
    try:
        await interaction.response.send_message("Pong! 🏓")
        logger.info(f"Ping command used by {interaction.user} in {interaction.guild}")
    except Exception as e:
        logger.error(f"Error in ping command: {e}")
        try:
            await interaction.response.send_message("Sorry, something went wrong!", ephemeral=True)
        except:
            logger.error("Failed to send error message")

@bot.tree.command(name="play", description="Putar musik dari YouTube")
async def play(interaction: discord.Interaction, query: str):
    """Play music from YouTube"""
    try:
        logger.info(f"Play command called by {interaction.user} with query: {query}")
        
        # Check if user is in a voice channel
        if not interaction.user.voice:
            logger.info(f"User {interaction.user} not in voice channel")
            await interaction.response.send_message("Kamu harus berada di voice channel untuk menggunakan command ini!", ephemeral=True)
            return
        
        voice_channel = interaction.user.voice.channel
        guild_id = interaction.guild.id
        logger.info(f"Voice channel: {voice_channel}, Guild ID: {guild_id}")
        
        # Initialize queue for this guild if not exists
        if guild_id not in music_queues:
            music_queues[guild_id] = deque()
        
        # Respond immediately to avoid timeout
        await interaction.response.send_message(f"🔍 Mencari lagu: **{query}**...")
        
        # Try to extract video info
        try:
            # If query is not a URL, search for it on YouTube
            if not query.startswith(('http://', 'https://', 'www.')):
                search_query = f"ytsearch:{query}"
                logger.info(f"Searching YouTube for: {search_query}")
            else:
                search_query = query
                logger.info(f"Using direct URL: {search_query}")
            
            data = await asyncio.get_event_loop().run_in_executor(None, lambda: ytdl.extract_info(search_query, download=False))
            logger.info(f"YouTube data extracted successfully")
            
            if 'entries' in data:
                data = data['entries'][0]
                logger.info(f"Using first entry from search results")
            
            title = data.get('title', 'Unknown')
            duration = data.get('duration', 0)
            actual_url = data.get('webpage_url', data.get('url', query))
            logger.info(f"Song info - Title: {title}, Duration: {duration}, URL: {actual_url}")
            
            # Add to queue
            music_queues[guild_id].append({
                'url': actual_url,
                'title': title,
                'duration': duration,
                'requester': interaction.user.mention
            })
            logger.info(f"Added to queue. Queue length: {len(music_queues[guild_id])}")
            
            # Connect to voice channel if not already connected
            if not interaction.guild.voice_client:
                logger.info(f"Connecting to voice channel: {voice_channel}")
                voice_client = await voice_channel.connect()
                logger.info(f"Successfully connected to voice channel")
            else:
                voice_client = interaction.guild.voice_client
                if voice_client.channel != voice_channel:
                    logger.info(f"Moving to voice channel: {voice_channel}")
                    await voice_client.move_to(voice_channel)
            
            # If nothing is playing, start playing
            if not voice_client.is_playing():
                logger.info("Starting to play music...")
                await play_next_song(interaction.guild)
                await interaction.edit_original_response(content=f"🎵 Sekarang memutar: **{title}**")
            else:
                logger.info("Music already playing, added to queue")
                await interaction.edit_original_response(content=f"🎵 Ditambahkan ke queue: **{title}**")
                
        except Exception as e:
            logger.error(f"Error processing YouTube query '{query}': {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            await interaction.edit_original_response(content=f"❌ Tidak bisa memproses '{query}'. Error: {str(e)[:200]}...")
            
    except Exception as e:
        logger.error(f"Error in play command: {e}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        try:
            await interaction.followup.send("Terjadi kesalahan saat memproses command!", ephemeral=True)
        except:
            logger.error("Failed to send error message")

@bot.tree.command(name="stop", description="Berhenti memutar musik dan keluar dari voice channel")
async def stop(interaction: discord.Interaction):
    """Stop playing music and disconnect from voice channel"""
    try:
        voice_client = interaction.guild.voice_client
        guild_id = interaction.guild.id
        
        if voice_client:
            # Clear queue
            if guild_id in music_queues:
                music_queues[guild_id].clear()
            
            # Stop playing and disconnect
            voice_client.stop()
            await voice_client.disconnect()
            await interaction.response.send_message("🔇 Musik dihentikan dan bot keluar dari voice channel.")
        else:
            await interaction.response.send_message("Bot tidak sedang terhubung ke voice channel.", ephemeral=True)
            
    except Exception as e:
        logger.error(f"Error in stop command: {e}")
        await interaction.response.send_message("Terjadi kesalahan saat menghentikan musik!", ephemeral=True)

@bot.tree.command(name="skip", description="Skip lagu yang sedang diputar")
async def skip(interaction: discord.Interaction):
    """Skip the currently playing song"""
    try:
        voice_client = interaction.guild.voice_client
        
        if voice_client and voice_client.is_playing():
            voice_client.stop()
            await interaction.response.send_message("⏭️ Lagu di-skip!")
        else:
            await interaction.response.send_message("Tidak ada lagu yang sedang diputar.", ephemeral=True)
            
    except Exception as e:
        logger.error(f"Error in skip command: {e}")
        await interaction.response.send_message("Terjadi kesalahan saat skip lagu!", ephemeral=True)

@bot.tree.command(name="queue", description="Lihat daftar lagu dalam queue")
async def queue(interaction: discord.Interaction):
    """Show the current music queue"""
    try:
        guild_id = interaction.guild.id
        
        if guild_id not in music_queues or not music_queues[guild_id]:
            await interaction.response.send_message("Queue kosong.", ephemeral=True)
            return
        
        queue_list = []
        for i, song in enumerate(list(music_queues[guild_id])[:10], 1):  # Show first 10 songs
            queue_list.append(f"{i}. **{song['title']}** - {song['requester']}")
        
        queue_text = "\n".join(queue_list)
        if len(music_queues[guild_id]) > 10:
            queue_text += f"\n... dan {len(music_queues[guild_id]) - 10} lagu lainnya"
        
        embed = discord.Embed(title="🎵 Music Queue", description=queue_text, color=0x0099ff)
        await interaction.response.send_message(embed=embed)
        
    except Exception as e:
        logger.error(f"Error in queue command: {e}")
        await interaction.response.send_message("Terjadi kesalahan saat menampilkan queue!", ephemeral=True)

async def play_next_song(guild):
    """Play the next song in the queue"""
    try:
        logger.info(f"play_next_song called for guild: {guild.name}")
        
        if guild.id not in music_queues or not music_queues[guild.id]:
            logger.info("No songs in queue")
            return
        
        voice_client = guild.voice_client
        if not voice_client:
            logger.error("No voice client available")
            return
        
        # Get next song from queue
        next_song = music_queues[guild.id].popleft()
        logger.info(f"Playing next song: {next_song['title']} - {next_song['url']}")
        
        # Create audio source
        try:
            logger.info(f"Creating audio source for URL: {next_song['url']}")
            player = await YTDLSource.from_url(next_song['url'], loop=bot.loop, stream=True)
            logger.info(f"Audio source created successfully with title: {player.title}")
        except Exception as e:
            logger.error(f"Error creating audio source: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            # Try to play next song if current fails
            if music_queues[guild.id]:
                await play_next_song(guild)
            return
        
        # Play the song
        def after_playing(error):
            if error:
                logger.error(f"Player error: {error}")
            else:
                logger.info(f"Finished playing: {next_song['title']}")
            
            # Play next song in queue
            coro = play_next_song(guild)
            fut = asyncio.run_coroutine_threadsafe(coro, bot.loop)
            try:
                fut.result()
            except Exception as e:
                logger.error(f"Error playing next song: {e}")
        
        # Check if opus is loaded before playing
        if not discord.opus.is_loaded():
            logger.error("Opus not loaded, cannot play audio")
            return
        
        voice_client.play(player, after=after_playing)
        logger.info(f"Now playing: {next_song['title']} in {guild.name}")
        logger.info(f"Voice client is_playing: {voice_client.is_playing()}, is_paused: {voice_client.is_paused()}")
        logger.info(f"Opus loaded: {discord.opus.is_loaded()}")
        
    except Exception as e:
        logger.error(f"Error in play_next_song: {e}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")

@bot.tree.command(name="nowplaying", description="Lihat lagu yang sedang diputar")
async def nowplaying(interaction: discord.Interaction):
    """Show currently playing song"""
    try:
        voice_client = interaction.guild.voice_client
        
        if voice_client and voice_client.is_playing():
            # Get current song info from voice client source
            source = voice_client.source
            if hasattr(source, 'title') and source.title:
                await interaction.response.send_message(f"🎵 Sedang memutar: **{source.title}**")
            else:
                await interaction.response.send_message("🎵 Sedang memutar musik...")
        else:
            await interaction.response.send_message("Tidak ada lagu yang sedang diputar.", ephemeral=True)
            
    except Exception as e:
        logger.error(f"Error in nowplaying command: {e}")
        await interaction.response.send_message("Terjadi kesalahan saat menampilkan lagu!", ephemeral=True)

@bot.tree.command(name="test", description="Test command untuk debugging")
async def test(interaction: discord.Interaction):
    """Test command for debugging"""
    try:
        logger.info(f"Test command called by {interaction.user}")
        opus_status = "✅ Loaded" if discord.opus.is_loaded() else "❌ Not loaded"
        await interaction.response.send_message(f"✅ Bot berfungsi dengan baik!\n🔊 Opus status: {opus_status}")
        logger.info("Test command executed successfully")
    except Exception as e:
        logger.error(f"Error in test command: {e}")
        await interaction.response.send_message("❌ Terjadi kesalahan!", ephemeral=True)

@bot.tree.command(name="volume", description="Atur volume musik (0-100)")
async def volume(interaction: discord.Interaction, volume: int):
    """Set music volume"""
    try:
        if volume < 0 or volume > 100:
            await interaction.response.send_message("Volume harus antara 0-100!", ephemeral=True)
            return
        
        voice_client = interaction.guild.voice_client
        if voice_client and voice_client.source:
            voice_client.source.volume = volume / 100
            await interaction.response.send_message(f"🔊 Volume diatur ke {volume}%")
        else:
            await interaction.response.send_message("Tidak ada musik yang sedang diputar.", ephemeral=True)
    except Exception as e:
        logger.error(f"Error in volume command: {e}")
        await interaction.response.send_message("Terjadi kesalahan saat mengatur volume!", ephemeral=True)

@bot.event
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    """Handle slash command errors"""
    logger.error(f"Command error: {error}")
    
    if not interaction.response.is_done():
        try:
            await interaction.response.send_message(
                "An error occurred while processing your command. Please try again later.",
                ephemeral=True
            )
        except:
            logger.error("Failed to send error response")

@bot.event
async def on_error(event, *args, **kwargs):
    """Handle general bot errors"""
    logger.error(f"An error occurred in {event}: {args}, {kwargs}")

async def main():
    """Main function to run the bot"""
    # Get bot token from environment variables
    token = os.getenv('DISCORD_BOT_TOKEN')
    
    if not token:
        logger.error("DISCORD_BOT_TOKEN environment variable not found!")
        logger.error("Please set your Discord bot token in the environment variables.")
        logger.error("You can create a .env file with: DISCORD_BOT_TOKEN=your_token_here")
        return
    
    try:
        # Start the bot
        logger.info("Starting Discord bot...")
        await bot.start(token)
    except discord.LoginFailure:
        logger.error("Invalid bot token provided!")
    except discord.ConnectionClosed:
        logger.error("Connection to Discord was closed unexpectedly")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
    finally:
        if not bot.is_closed():
            await bot.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
