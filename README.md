# ClutchClock ⏰

ClutchClock is a smart, customizable bomb timer overlay for tactical shooters like Valorant and CS2. It uses passive, non-invasive screen recognition to automatically detect when the bomb is planted and displays a highly visible, configurable timer to help you make game-winning decisions in those crucial final seconds.

This tool is designed to be safe and compliant with anti-cheat systems like Vanguard and VAC, as it does not read game memory or modify any game files.

## Features

* **Automatic Detection:** Passively scans a user-defined area of the screen to automatically start the timer when the bomb is planted.
* **Multi-Game Support:** Easily switch between profiles for Valorant (45s Spike) and CS2 (40s C4) with game-specific settings.
* **Fully Customizable Overlay:**
    * Adjust the timer's font size and transparency.
    * Move the timer anywhere on your screen.
* **Global Hotkeys:** Adjust the timer or stop it manually with keyboard shortcuts that work even when you're in-game.
* **Intuitive GUI:** A clean, modern interface to manage all settings, including:
    * A live log to see app status.
    * A real-time confidence meter for the visual scanner.
    * An interactive tool to easily calibrate the screen capture region.
* **Persistent Settings:** All your customizations are saved in a `config.json` file and loaded automatically.

## How to Use

1.  **Prepare Template Images:** You need to provide two images for the app to work:
    * `valorant_spike.png`: A screenshot of just the red Spike icon that appears when the Spike is planted in Valorant.
    * `cs2_c4.png`: A screenshot of just the C4 icon that appears when the C4 is planted in CS2.
    * Place both of these `.png` files in the same folder as the application.

2.  **Run the Application:**
    * Launch `ClutchClock.exe`.
    * **Important:** For the global hotkeys to work in-game, you must **run the application as an administrator**. Right-click `ClutchClock.exe` and select "Run as administrator".

3.  **Calibrate:**
    * The first time you run the app for a new game, click the **"Calibrate Capture Region"** button.
    * Click and drag a box around the area on your screen where the bomb plant icon appears.
    * Press **ENTER** to save. The app will now only scan this small area, making it very efficient.

4.  **Customize:**
    * Use the sliders and buttons in the settings window to adjust the timer's appearance and hotkeys to your liking.

## Disclaimer

This application is designed to be safe and avoid triggering anti-cheat systems like Vanguard and VAC. It operates by passively reading screen pixels and listening for keyboard inputs, similar to other legitimate software like OBS and Discord. It **does not** read game memory, modify game files, or send any automated inputs to the game.

However, the use of any third-party application is at your own risk. The creator is not responsible for any account suspensions.
