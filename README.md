# RL_projects
reinforcement learning tutorial project

## Installation 
Create your own virtual enviroment
```
conda create -n RL_env python=3.9 jupyter
conda activate RL_env
conda install pytorch torchvision cudatoolkit=10.2 -c pytorch
pip install -r requirements.txt
```

## Download Atari ROMS
To play atari games, you will need to download ROMs since OpenAI no longer provides these by default. 
wget http://www.atarimania.com/roms/Roms.rar

Read this [article](https://retro.readthedocs.io/en/latest/getting_started.html) to know more about this issue. 

After you download ROMs are unzipped them, you can import import these ROMs by

```
python -m retro.import /path/to/your/ROMS/directory/
```

Here is one of the game that is included by default and the command you can start an emulator to play it.

```
python -m retro.examples.interactive --game SpaceInvaders-Atari2600
```
