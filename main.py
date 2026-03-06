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
                    description="Sorry, we were unable to verify your registration. Please make sure you have answered the Discord username question in your ticket. You can update your responses by following the link that was sent to your email after registering, or by retrieving it on [lookup.tito.io](https://lookup.tito.io).\n\nIf you believe this is an error, please let an organiser know.",
                    color=discord.Colour.red(),
                ),
                ephemeral=True,
            )
            return

        first_name: str = ticket["ticket_name"]
        ref: str = ticket["ticket_reference"]

        ticket_role: discord.Role | None = discord.utils.get(
            guild.roles, name=ticket["release_name"]
        )

        if not ticket_role:
            logger.warning(
                "Failed to find a discord role with name %s for user %s (%d)",
                ticket["release_name"],
                discord_username,
                user_id,
            )
            await interaction.followup.send(
                embed=discord.Embed(
                    description="Sorry, we were unable to verify your registration because we couldn't find the appropriate role to assign you. Please let an organiser know.",
                    color=discord.Colour.red(),
                ),
                ephemeral=True,
            )
            return

        try:
            await member.add_roles(ticket_role)
        except discord.Forbidden:
            logger.error(
                f"Failed to assign role to user {discord_username} ({user_id}) due to insufficient permissions"
            )
            await interaction.followup.send(
                embed=discord.Embed(
                    description="Sorry, we were unable to assign you the verified role. Please let an organiser know.",
                    color=discord.Colour.red(),
                ),
                ephemeral=True,
            )
            return

        logger.info(
            f"Discord account {discord_username} ({user_id}) linked to ticket {ref}"
        )

        try:
            await member.edit(nick=first_name)
        except discord.Forbidden:
            logger.error(
                f"Failed to update nickname for user {discord_username} ({user_id}) due to insufficient permissions"
            )

        await interaction.followup.send(
            embed=discord.Embed(
                description=f"**Welcome {first_name}, you have been successfully verified!** You can now view the other channels in the server - we recommend introducing yourself to everybody else in the introductions channel.\n\nWe encourage everybody to use their real name as their nickname, so your nickname has been automatically updated to your first name. However, if you are not comfortable with this, or simply want to use a different name, then feel free to update it to something else.",
                color=discord.Colour.green(),
            ),
            ephemeral=True,
        )
        return


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

    guild: discord.Guild = ctx.guild

    await fetch_tickets_from_api()

    for member in guild.members:
        if member.bot:
            continue

        ticket: Ticket | None = await get_ticket_from_discord_tag(member.name)

        if not ticket:
            continue

        ticket_role: discord.Role | None = discord.utils.get(
            guild.roles, name=ticket["release_name"]
        )

        if not ticket_role or ticket_role in member.roles:
            continue

        try:
            await member.add_roles(ticket_role)
            await member.edit(nick=ticket["ticket_name"])
        except discord.Forbidden:
            logger.error(
                f"Failed to assign role to user {member.name} ({member.id}) due to insufficient permissions"
            )
            continue

    await ctx.followup.send("Finished checking all users.")


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
