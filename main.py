import discord
import logging
from yaml import safe_load
import requests
import sys
from discord.ui import View
from typing import Final


logger = logging.getLogger()
logger.setLevel(logging.INFO)


bot = discord.Bot(intents=discord.Intents.all())  # type: ignore[no-untyped-call]


ticket_cache: dict[str, str] = {}


with open("config.yaml", "r") as config_file:
    config = safe_load(config_file)


account_slug: Final[str] = config["tito"]["account-slug"]
event_slug: Final[str] = config["tito"]["event-slug"]
question_slug: Final[str] = config["tito"]["question-slug"]
api_endpoint: Final[str] = (
    f"https://api.tito.io/v3/{account_slug}/{event_slug}/questions/{question_slug}/answers"
)


def get_ticket_from_discord_tag(discordtag: str) -> dict[str, str] | None:
    response = requests.get(
        url=api_endpoint,
        headers={
            "Authorization": f"Token token={config['tito']['token']}",
            "User-Agent": "birmingBot (contact email css@guild.bham.ac.uk)",
        },
    )
    data = response.json()
    tickets = [ticket for ticket in data["answers"] if ticket["response"] == discordtag]

    if not tickets:
        logger.warning("No ticket found for Discord tag %s", discordtag)
        return None

    return dict(tickets[0])


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
        discord_username: str = interaction.user.name
        user_id: int = interaction.user.id

        async def grant_access() -> discord.Embed:
            try:
                guild: discord.Guild = await bot.fetch_guild(config["guild-id"])
                member: discord.Member = await guild.fetch_member(user_id)
                role_id: int = int(config["role-id"])

                if member.get_role(role_id) is not None:
                    logger.debug(
                        "User %s already has role %d", discord_username, role_id
                    )
                    return discord.Embed(
                        description="You have already been verified!",
                        color=discord.Colour.red(),
                    )

                role: discord.Role | None = guild.get_role(role_id)

                if not role:
                    logger.info(
                        f"Role with ID {role_id} not found in guild {guild.id} while trying to verify {discord_username}"
                    )
                    logger.debug(f"Guild roles: {guild.roles}")
                    return discord.Embed(
                        description="Verification role not found. Please contact an organiser.",
                        color=discord.Colour.red(),
                    )

                ticket: dict[str, str] | None = get_ticket_from_discord_tag(
                    discordtag=discord_username
                )

                if ticket is None:
                    logger.warning(
                        "Failed to verify user %s (%d) because no ticket data was returned",
                        discord_username,
                        user_id,
                    )
                    return discord.Embed(
                        description="Sorry, we were unable to verify your registration. Please make sure you have answered the Discord username question in your ticket. You can update your responses by following the link that was sent to your email after registering, or by retrieving it on [lookup.tito.io](https://lookup.tito.io).\n\nIf you believe this is an error, please let an organiser know.",
                        color=discord.Colour.red(),
                    )

                first_name: str = ticket["ticket_name"].split(" ")[0]
                ref: str = ticket["ticket_reference"]

                await member.add_roles(role)

                logger.info(
                    f"Discord account {discord_username} ({user_id}) linked to ticket {ref}"
                )

                await member.edit(nick=first_name)

                return discord.Embed(
                    description=f"**Welcome {first_name}, you have been successfully verified!** You can now view the other channels in the server - we recommend introducing yourself to everybody else in the introductions channel.\n\nWe encourage everybody to use their real name as their nickname, so your nickname has been automatically updated to your first name. However, if you are not comfortable with this, or simply want to use a different name, then feel free to update it to something else.",
                    color=discord.Colour.green(),
                )
            except Exception as exception:
                logger.exception(f"Error while verifying {discord_username}")
                logger.debug(exception.with_traceback(sys.exc_info()[2]))
                logger.debug(str(exception))
                return discord.Embed(
                    description="An unexpected error occurred while trying to verify you. Please let an organiser know.",
                    color=discord.Colour.red(),
                )

        await interaction.response.send_message(
            embed=await grant_access(), ephemeral=True
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


if __name__ == "__main__":
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    bot.run(config["bot"]["token"])
