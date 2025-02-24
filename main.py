from strictyaml import load
import discord
import logging
import requests
import sys

with open("config.yaml", "r") as f:
    config = load(f.read()).data

logger = logging.getLogger()
logger.setLevel(logging.INFO)

bot = discord.Bot(intents=discord.Intents.all())

def is_account_registered(discordtag):
    response = requests.get(f"https://api.tito.io/v3/{config['tito']['account-slug']}/{config['tito']['event-slug']}/questions/{config['tito']['question-slug']}/answers", headers={"Authorization": f"Token token={config['tito']['token']}", "User-Agent": "birmingBot (contact email css@guild.bham.ac.uk)"})
    data = response.json()
    tickets = list(filter(lambda ticket: ticket["response"].lower() == discordtag and len(ticket["response"]) > 2, data["answers"]))
    if len(tickets) == 0:
        return False
    return tickets[0]

class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None) 

    @discord.ui.button(label="Get Access", custom_id="btn-access", style=discord.ButtonStyle.success)
    async def button_callback(self, button, interaction):
        user = interaction.user.name
        user_id = interaction.user.id

        async def grant_access():
            embed = discord.Embed(
                description="Sorry, we were unable to verify your registration. Please make sure you have answered the Discord username question in your ticket. You can update your responses by following the link that was sent to your email after registering, or by retrieving it on [lookup.tito.io](https://lookup.tito.io).\n\nIf you believe this is an error, please let an organiser know.",
                color=discord.Colour.red(), 
            )

            try:
                ticket = is_account_registered(user)
                if ticket == False:
                    pass
                else:
                    first_name = ticket["ticket_name"].split(" ")[0]
                    ref = ticket["ticket_reference"]
                    guild = await bot.fetch_guild(config["guild-id"])
                    member = await guild.fetch_member(user_id)
                    role_id = int(config["role-id"])

                    if member.get_role(role_id) != None:
                        return discord.Embed(
                            description="You have already been verified!",
                            color=discord.Colour.red(), 
                        )
                    role = guild.get_role(role_id)

                    await member.add_roles(role)
                    logger.info(f"Discord account {user} ({user_id}) linked to ticket {ref}")
                    embed = discord.Embed(
                        description=f"**Welcome {first_name}, you have been successfully verified!** You can now view the other channels in the server - we recommend introducing yourself to everybody else in the introductions channel.\n\nWe encourage everybody to use their real name as their nickname, so your nickname has been automatically updated to your first name. However, if you are not comfortable with this, or simply want to use a different name, then feel free to update it to something else.",
                        color=discord.Colour.green(), 
                    )

                    await member.edit(nick=first_name)
            except Exception:
                logger.exception(f"Error while verifying {user}")

            return embed


        await interaction.response.send_message(embed=await grant_access(), ephemeral=True)

@bot.event
async def on_ready():
    bot.add_view(VerifyView()) 
    logging.info("Bot is ready")

@bot.slash_command()
async def button(ctx):
    chan = await bot.fetch_channel(config["chan-id"])
    embed = discord.Embed(
        title="Access",
        description="When you have finished reading the rules and the [birmingHack Code of Conduct](https://birminghack.com/conduct), please use the button below gain access to the rest of the server.\n\nYou will need to make sure you have answered the Discord username question when registering for birmingHack - if you haven't, then you can update your responses by following the link that was sent to your email after registering a ticket. \n\nYour Discord nickname will also be automatically updated to your first name after using the button. If you do not wish to use your real name as your nickname, feel free to update this afterwards.",
        color=discord.Colour.green(), 
    )
    await chan.send(embed=embed, view=VerifyView())
    await ctx.respond(f"Message sent to {chan.mention}", ephemeral=True)

if __name__ == "__main__":
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    bot.run(config["bot"]["token"])

