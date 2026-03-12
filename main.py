import discord
import logging
from yaml import safe_load
import sys
import aiohttp
from discord.ui import View
from typing import Final, Optional, TypedDict
from discord_logging.handler import DiscordHandler


logger = logging.getLogger()
logger.setLevel(logging.INFO)


bot = discord.Bot(intents=discord.Intents.all())  # type: ignore[no-untyped-call]


with open("config.yaml", "r") as config_file:
    config = safe_load(config_file)


account_slug: Final[str] = config["tito"]["account-slug"]
event_slug: Final[str] = config["tito"]["event-slug"]
question_slug: Final[str] = config["tito"]["question-slug"]
answers_endpoint: Final[str] = (
    f"https://api.tito.io/v3/{account_slug}/{event_slug}/questions/{question_slug}/answers?page[size]=1000&expand=ticket"
)

releases_endpoint: Final[str] = (
    f"https://api.tito.io/v3/{account_slug}/{event_slug}/releases?version=3.1"
)


class Ticket(TypedDict):
    ticket_reference: str
    ticket_name: str
    discord_username_response: str
    release_name: str


answer_cache: list[Ticket] = []
releases_cache: dict[int, str] = {}


def check_cache_for_discord_tag(discordtag: str) -> Optional[Ticket]:
    for ticket in answer_cache:
        if (
            ticket["discord_username_response"].strip().lower()
            == discordtag.strip().lower()
        ):
            return ticket
    return None


async def fetch_tickets_from_api() -> None:
    await fetch_releases_from_api()
    async with (
        aiohttp.ClientSession(
            headers={
                "Authorization": f"Token token={config['tito']['token']}",
                "User-Agent": "birmingBot (contact email css@guild.bham.ac.uk)",
            },
        ) as http_session,
        http_session.get(answers_endpoint) as response,
    ):
        data = await response.json()
        answer_cache.clear()
        answer_cache.extend(
            {
                "ticket_reference": answer["ticket"]["reference"],
                "ticket_name": answer["ticket"]["first_name"],
                "discord_username_response": answer["response"],
                "release_name": releases_cache[answer["ticket"]["release_id"]],
            }
            for answer in data["answers"]
        )


async def fetch_releases_from_api() -> None:
    async with (
        aiohttp.ClientSession(
            headers={
                "Authorization": f"Token token={config['tito']['token']}",
                "User-Agent": "birmingBot (contact email css@guild.bham.ac.uk)",
            },
        ) as http_session,
        http_session.get(releases_endpoint) as response,
    ):
        data = await response.json()
        releases_cache.clear()
        releases_cache.update(
            {release["id"]: release["title"] for release in data["releases"]}
        )


async def get_ticket_from_discord_tag(discordtag: str) -> Optional[Ticket]:
    if cached_ticket := check_cache_for_discord_tag(discordtag):
        logger.debug("Cache hit for Discord tag %s", discordtag)
        return cached_ticket

    await fetch_tickets_from_api()

    ticket: Ticket | None = check_cache_for_discord_tag(discordtag)

    if not ticket:
        logger.warning("No ticket found for Discord tag %s", discordtag)
        return None

    logger.debug("Ticket found for Discord tag %s: %s", discordtag, ticket)

    return ticket

async def assign_ticket_role_and_nick(member: discord.Member, ticket: Ticket) -> bool:
    """Applies the appropriate role and nickname for a verified user."""
    guild: discord.Guild = member.guild
    ticket_role: discord.Role | None = discord.utils.get(
        guild.roles, name=ticket["release_name"]
    )

    if not ticket_role:
        logger.warning(
            "Failed to find a discord role with name %s for user %s (%d)",
            ticket["release_name"],
            member.name,
            member.id,
        )
        return False

    try:
        await member.add_roles(ticket_role)
    except discord.Forbidden:
        logger.error(
            "Failed to assign role to user %s (%d) due to insufficient permissions",
            member.name, 
            member.id
        )
        return False

    logger.info(
        "Discord account %s (%d) successfully verified with ticket %s", 
        member.name, 
        member.id, 
        ticket["ticket_reference"]
    )

    try:
        await member.edit(nick=ticket["ticket_name"])
    except discord.Forbidden:
        logger.error(
            "Failed to update nickname for user %s (%d) due to insufficient permissions",
            member.name, 
            member.id
        )

    return True


class VerifyView(View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Get Access", custom_id="btn-access", style=discord.ButtonStyle.success
    )
    async def button_callback(  # type: ignore[misc]
        self: View, button: discord.Button, interaction: discord.Interaction
    ) -> None:
        if not interaction.user:
            return

        await interaction.response.defer(ephemeral=True)

        discord_username: str = interaction.user.name
        user_id: int = interaction.user.id

        guild: discord.Guild = await bot.fetch_guild(config["guild-id"])
        member: discord.Member = await guild.fetch_member(user_id)

        if len(member.roles) > 1:
            logger.debug("User %s already has multiple roles.", discord_username)
            await interaction.followup.send(
                embed=discord.Embed(
                    description="You have already been verified!",
                    color=discord.Colour.red(),
                ),
                ephemeral=True,
            )
            return

        ticket: Ticket | None = await get_ticket_from_discord_tag(
            discordtag=discord_username
        )

        if ticket is None:
            logger.warning(
                "Failed to verify user %s (%d) because no ticket data was returned",
                discord_username,
                user_id,
            )
            await interaction.followup.send(
                embed=discord.Embed(
                    description="Sorry, we were unable to verify your registration. Please make sure you have answered the Discord username question in your ticket...",
                    color=discord.Colour.red(),
                ),
                ephemeral=True,
            )
            return

        success = await assign_ticket_role_and_nick(member, ticket)

        if not success:
            await interaction.followup.send(
                embed=discord.Embed(
                    description="Sorry, we were unable to verify your registration due to a server error. Please let an organiser know.",
                    color=discord.Colour.red(),
                ),
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            embed=discord.Embed(
                description=f"**Welcome {ticket['ticket_name']}, you have been successfully verified!** You can now view the other channels in the server...",
                color=discord.Colour.green(),
            ),
            ephemeral=True,
        )


@bot.event
async def on_ready() -> None:
    bot.add_view(VerifyView())
    logging.info("Bot is ready")


@bot.slash_command(
    name="send-verification-button",
    description="Sends the verification button to the verification channel specified in config.",
)  # type: ignore[misc, no-untyped-call]
async def send_verification_button(ctx: discord.ApplicationContext) -> None:  # type: ignore[misc]
    channel = await bot.fetch_channel(config["verification-channel-id"])
    if not (isinstance(channel, discord.TextChannel)):
        await ctx.respond(
            "The specified channel is not a text channel.", ephemeral=True
        )
        return
    embed = discord.Embed(
        title="Access",
        description="When you have finished reading the rules and the [birmingHack Code of Conduct](https://birminghack.com/conduct), please use the button below gain access to the rest of the server.\n\nYou will need to make sure you have answered the Discord username question when registering for birmingHack - if you haven't, then you can update your responses by following the link that was sent to your email after registering a ticket. \n\nYour Discord nickname will also be automatically updated to your first name after using the button. If you do not wish to use your real name as your nickname, feel free to update this afterwards.",
        color=discord.Colour.green(),
    )
    await channel.send(embed=embed, view=VerifyView())
    await ctx.respond(f"Message sent to {channel.mention}", ephemeral=True)


@bot.slash_command(
    name="check-all-users",
    description="Checks all users in the guild against the ticket database and grants roles as needed.",
)  # type: ignore[misc, no-untyped-call]
async def check_all_users(ctx: discord.ApplicationContext) -> None:  # type: ignore[misc]
    await ctx.defer(ephemeral=True)

    if not ctx.guild:
        return

    await fetch_tickets_from_api()

    for member in ctx.guild.members:
        if member.bot or len(member.roles) > 1:
            continue

        ticket: Ticket | None = await get_ticket_from_discord_tag(member.name)
        if ticket:
            # --- USE THE HELPER HERE ---
            await assign_ticket_role_and_nick(member, ticket)

    await ctx.followup.send("Finished checking all users.")


@bot.event
async def on_member_join(member: discord.Member) -> None:
    if member.bot:
        return

    logger.info("User %s joined the server, attempting auto-verification.", member.name)

    ticket: Ticket | None = await get_ticket_from_discord_tag(discordtag=member.name)

    if ticket:
        success: bool = await assign_ticket_role_and_nick(member, ticket)
        if success:
            try:
                await member.send(
                    f"**Welcome to the server, {ticket['ticket_name']}!**\n\n"
                    "We found your ticket and have automatically verified you. "
                    "Your nickname has been updated and you now have access to the server channels."
                )
            except discord.Forbidden:
                pass # User has DMs disabled
    else:
        logger.info("Auto-verification skipped: No ticket found for newly joined user %s.", member.name)


if __name__ == "__main__":
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    discord_logging_handler: logging.Handler = DiscordHandler(
        service_name="birmingBot",
        webhook_url=config["discord-log-channel-webhook"],
    )

    logger.addHandler(discord_logging_handler)

    bot.run(config["bot"]["token"])
