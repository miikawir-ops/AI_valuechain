import os
import subprocess
import argparse

def setup_repo(username):
    # Alustetaan git
    subprocess.run(["git", "init"])
    
    # Lisätään kaikki tiedostot (paitsi .gitignoreen listatut)
    subprocess.run(["git", "add", "."])
    
    # Commit
    subprocess.run(["git", "commit", "-m", "Initial commit of AI Stock Agent"])
    
    # Ohje käyttäjälle
    print(f"\n--- Seuraava vaihe ---")
    print(f"1. Mene GitHub.comiin ja luo uusi tyhjä repository nimeltä 'ai-stock-agent'")
    print(f"2. Aja seuraavat komennot terminaalissa:")
    print(f"git remote add origin https://github.com/{username}/ai-stock-agent.git")
    print(f"git branch -M main")
    print(f"git push -u origin main")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup", action="store_true")
    parser.add_argument("--username")
    args = parser.parse_args()
    
    if args.setup:
        setup_repo(args.username)