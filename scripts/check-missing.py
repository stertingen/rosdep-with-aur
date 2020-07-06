#!/usr/bin/env python3
#
# Copyright (c) 2020 Hermann von Kleist
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.


import yaml
import tarfile
import urllib.request
import io
import gzip
import json


def list_official_packages():
    pkgs = []
    for repo in ['core', 'extra', 'community']:
        with tarfile.open('/var/lib/pacman/sync/{}.db'.format(repo), mode='r:gz') as db:
            for m in db.getmembers():
                if m.isfile():
                    next_line_has_name = False
                    for raw_line in db.extractfile(m).readlines():
                        line = raw_line.decode('utf-8').strip()
                        if next_line_has_name:
                            if len(line) > 0:
                                pkgs.append(line)
                            else:
                                next_line_has_name = False
                        elif '%NAME%' in line:
                            next_line_has_name = True
                        elif '%PROVIDES%' in line:
                            next_line_has_name = True
    return set(pkgs)


def list_aur_packages():
    with urllib.request.urlopen('https://aur.archlinux.org/packages.gz') as res:
        stream = io.BytesIO(res.read())
        file = gzip.GzipFile(fileobj=stream)
        return set([line.decode('utf-8').strip() for line in file.readlines()])


def check_repology(key, rosdep_mappings):
    # Map rosdep os names to repology os identifiers
    os_lut = {
        'debian': {
            '*': 'debian_stable',
            'stretch': 'debian_oldstable',
            'buster': 'debian_stable',
        },
        'ubuntu': {
            '*': 'ubuntu_20_04',
            'bionic': 'ubuntu_18_04',
            'focal': 'ubuntu_20_04',
            'trusty': 'ubuntu_14_04',
            'xenial': 'ubuntu_16_04',
        }
    }

    def filter_hits(hits):
        hits = filter(lambda h: not h.endswith("-doc"), hits)
        hits = filter(lambda h: not h.endswith("-docs"), hits)
        hits = filter(lambda h: not h.endswith("-demos"), hits)

        hits = filter(lambda h: not h.endswith("-git"), hits)
        hits = filter(lambda h: not h.endswith("-svn"), hits)
        hits = filter(lambda h: not h.endswith("-hg"), hits)

        if key.startswith('python-'):
            # When our key starts with python-, it's a python 2 package.
            # So exclude arch linux python 3 packages, which also start with python-. Yikes.
            hits = filter(lambda h: not h.startswith("python-"), hits)
        elif key.startswith('python3-'):
            hits = filter(lambda h: not h.startswith("python2-"), hits)
        return hits

    foreign_hits = {}
    for os in rosdep_mappings:
        if os in os_lut:
            if type(rosdep_mappings[os]) is dict:
                for osver in rosdep_mappings[os]:
                    if osver in os_lut[os] and rosdep_mappings[os][osver] is not None:
                        foreign_hits[os_lut[os][osver]] = rosdep_mappings[os][osver]
            elif rosdep_mappings[os] is not None:
                foreign_hits[os_lut[os]['*']] = rosdep_mappings[os]

    for os in foreign_hits:
        repo_hits = []
        aur_hits = []
        for pkgname in foreign_hits[os]:
            try:
                url = 'https://repology.org/tools/project-by?repo={}&name_type=binname&target_page=api_v1_project&name={}'.format(os, pkgname)
                with urllib.request.urlopen(url) as res:
                    data = json.loads(res.read())

                repo_hits.extend([d for d in data if d['repo'] == 'arch'])
                aur_hits.extend([d['binname'] for d in data if d['repo'] == 'aur'])
            except urllib.request.URLError:
                continue
            except yaml.YAMLError:
                continue

        core_hits = set([h['binname'] for h in repo_hits if h['subrepo'] == 'core'])
        if len(core_hits) > 0:
            return filter_hits(core_hits)

        extra_hits = set([h['binname'] for h in repo_hits if h['subrepo'] == 'extra'])
        if len(extra_hits) > 0:
            return filter_hits(extra_hits)

        community_hits = set([h['binname'] for h in repo_hits if h['subrepo'] == 'community'])
        if len(community_hits) > 0:
            return filter_hits(community_hits)

        if len(aur_hits) > 0:
            return filter_hits(set(aur_hits))

    return []


def main():
    print("Loading pacman packages ...")
    official_packages = list_official_packages()
    print("{} pacman packages loaded.".format(len(official_packages)))

    print("Loading AUR packages ...")
    aur_packages = list_aur_packages()
    print("{} AUR packages loaded.".format(len(aur_packages)))

    stats = {"official": 0, "aur": 0, "repology": 0, "n/a": 0}
    missing_keys = dict()
    for filename in ["base.yaml", "python.yaml"]:
        print("Loading {} ...".format(filename))
        with urllib.request.urlopen('https://raw.githubusercontent.com/ros/rosdistro/master/rosdep/{}'.format(filename)) as res:
            rd_map = yaml.safe_load(res.read())
            for key in rd_map:

                if 'arch' in rd_map[key]:
                    # Verify current rosdep keys
                    # TODO: Fix false negatives for pip entries!
                    if all([p in official_packages for p in rd_map[key]['arch']]):
                        key_is_valid = True
                    elif all([p in aur_packages for p in rd_map[key]['arch']]):
                        key_is_valid = True
                    else:
                        print("Invalid rosdep key: {}: [{}]".format(key, ', '.join(rd_map[key]['arch'])))
                        key_is_valid = False
                else:
                    key_is_valid = False

                if not key_is_valid:
                    if key.startswith('python-'):
                        guess = key.replace('python', 'python2', 1)
                    elif key.startswith('python3-'):
                        guess = key.replace('python3', 'python', 1)
                    else:
                        guess = key

                    print("Looking for key {} ...".format(key))

                    if guess in official_packages:
                        missing_keys[key] = {"arch": [guess]}
                        stats["official"] += 1
                    elif guess in aur_packages:
                        missing_keys[key] = {"arch": [guess]}
                        stats["aur"] += 1
                    else:
                        pkgs = list(check_repology(key, rd_map[key]))
                        if len(pkgs) > 0:
                            missing_keys[key] = {"arch": pkgs}
                            stats["repology"] += 1
                        else:
                            missing_keys[key] = {"arch": []}
                            stats["n/a"] += 1

    print("Stats: {} in official repositories, {} in AUR, {} found via repology, {} not found."
          .format(stats["official"], stats["aur"], stats["repology"], stats["n/a"]))
    with open('arch-with-aur.yaml', 'w') as out_file:
        yaml.dump(missing_keys, out_file)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
